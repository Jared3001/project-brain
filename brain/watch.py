"""The watcher: a scheduled agent that notices when the public record moves.

  python -m brain.watch                 check the sources, write a digest (downloads new files)
  python -m brain.watch --dry-run       check the sources, download nothing
  python -m brain.watch --replay 2026-10-15
                                        what a run on that day would have reported, using
                                        the corpus as it stands (documents published after
                                        the day form the "new" side)
  python -m brain.watch --narrate       add a short written summary (one model call, capped)
  python -m brain.watch --promote PATH  replace data/brain.db with a reviewed candidate

What one run does:
  1. Check ERCOT's GIS report list for a monthly report newer than the corpus, and
     TCEQ's live pending permit list for a change to the Kilby row.
  2. If anything is new, build a candidate database: a copy of data/brain.db with the
     new documents ingested and the record re-extracted. data/brain.db is never written.
  3. Diff candidate against current: record values that moved, new dated changes,
     consistency verdicts that flipped, deadline statuses that changed.
  4. Write a digest (JSON and Markdown) under data/watch/, every line cited.

Guard rails: read only against the live database by construction (it is opened read
only and only ever copied); a hard dollar cap on the optional model call, whose
numbers are checked against the diff before the summary is kept; every run, including
"nothing new", is logged to data/watch/runs.jsonl.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

from brain.registry import ROOT

WATCH = ROOT / "data" / "watch"
UA = {"User-Agent": "Mozilla/5.0 (project-brain watcher)"}
ERCOT_LIST = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId=15933"
ERCOT_DOC = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={}"
TCEQ_PENDING = "https://www.tceq.texas.gov/assets/public/permitting/air/reports/applications/nsr-pending-permits.html"
NARRATE_CAP_USD = 0.15


def _get(url: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _live_db() -> Path:
    from brain.ingest import DB_PATH
    return DB_PATH


# ---- 1. check the sources ---------------------------------------------------------

def check_ercot(download: bool) -> list[dict]:
    """GIS reports published after the newest one in corpus/ercot."""
    folder = ROOT / "corpus" / "ercot"
    have = sorted(p.name[:10] for p in folder.glob("*_ercot_gis_report.xlsx"))
    newest = have[-1] if have else "0000-00-00"
    docs = json.loads(_get(ERCOT_LIST))["ListDocsByRptTypeRes"]["DocumentList"]
    found = []
    for item in docs:
        d = item["Document"]
        date = d["PublishDate"][:10]
        if d["FriendlyName"].startswith("GIS_Report") and date > newest:
            dest = folder / f"{date}_ercot_gis_report.xlsx"
            if download and not dest.exists():
                dest.write_bytes(_get(ERCOT_DOC.format(d["DocID"]), timeout=300))
            found.append({"source": "ERCOT GIS report", "published": date, "path": str(dest.relative_to(ROOT)),
                          "downloaded": download})
    return sorted(found, key=lambda f: f["published"])


def _kilby_row(html_text: str) -> str:
    from brain.ingest import extract_tceq_pending
    tmp = WATCH / ".row.html"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(html_text)
    pages = extract_tceq_pending(tmp)
    tmp.unlink()
    # header names vary between the live page and archived snapshots; compare the values only
    return " | ".join(line.split(": ", 1)[-1] for line in (pages[0].text.splitlines() if pages else []))


def check_tceq(download: bool) -> list[dict]:
    """A change to the Kilby row on TCEQ's live pending permit list, versus the newest snapshot."""
    folder = ROOT / "corpus" / "wayback" / "tceq-pending"
    snaps = sorted(folder.glob("tceq-pending_*.html"))
    live = _get(TCEQ_PENDING).decode("utf-8", errors="ignore")
    now_row = _kilby_row(live)
    last_row = _kilby_row(snaps[-1].read_text(errors="ignore")) if snaps else ""
    status = {"source": "TCEQ pending permit list", "live_row": now_row, "last_snapshot": snaps[-1].name if snaps else None}
    if not now_row:
        return [{**status, "problem": "Kilby row not found on the live page"}]
    if now_row == last_row:
        return []
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    dest = folder / f"tceq-pending_{stamp}.html"
    if download:
        dest.write_text(live)
    return [{**status, "published": dt.date.today().isoformat(), "path": str(dest.relative_to(ROOT)),
             "downloaded": download, "previous_row": last_row}]


# ---- 2. build a candidate --------------------------------------------------------

def build_candidate(new_paths: list[str]) -> Path:
    """Copy the live database, ingest the new files into the copy, re-extract the record."""
    from brain import ingest, record
    from brain.registry import load_registry
    out = WATCH / "candidate.db"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_live_db(), out)
    reg = load_registry()
    wanted = {str((ROOT / p).resolve()) for p in new_paths}
    con = sqlite3.connect(out)
    for doc in reg.documents:
        if str(doc.path.resolve()) not in wanted:
            continue
        pages = [p for p in ingest.extract(doc) if p.text]
        con.execute("DELETE FROM pages WHERE doc_id=?", (doc.id,))
        con.execute("DELETE FROM documents WHERE id=?", (doc.id,))
        con.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (doc.id, doc.title, doc.kind, doc.tier, doc.publisher, doc.published.isoformat(), doc.url,
                     doc.rel, ingest._sha256(doc.path), len(pages), doc.notes))
        con.executemany("INSERT INTO pages (doc_id, page_no, locator, text, n_chars, low_text, ocr) VALUES (?,?,?,?,?,?,?)",
                        [(doc.id, p.page_no, p.locator, p.text, len(p.text), int(p.low_text), int(p.ocr)) for p in pages])
    con.execute("INSERT INTO pages_fts(pages_fts) VALUES ('rebuild')")
    con.commit()
    con.close()
    record.build(out)
    return out


def build_replay(day: dt.date) -> tuple[Path, Path, list[dict]]:
    """Baseline = the live database minus documents published after `day`; candidate = the live database."""
    from brain import record
    WATCH.mkdir(parents=True, exist_ok=True)
    base, cand = WATCH / "replay-base.db", WATCH / "replay-candidate.db"
    shutil.copyfile(_live_db(), base)
    shutil.copyfile(_live_db(), cand)
    con = sqlite3.connect(base)
    later = con.execute("SELECT id, title, published FROM documents WHERE published > ?", (day.isoformat(),)).fetchall()
    for doc_id, *_ in later:
        con.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
        con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    con.execute("INSERT INTO pages_fts(pages_fts) VALUES ('rebuild')")
    con.commit()
    con.close()
    record.build(base)
    new = [{"source": t, "published": p, "doc_id": i} for i, t, p in sorted(later, key=lambda r: r[2])]
    return base, cand, new


# ---- 3. diff ---------------------------------------------------------------------

@contextlib.contextmanager
def using_db(path: Path):
    """Point the read side (record, permit, claims, commitments) at another database for a moment."""
    import brain.ingest as ing
    from brain import claims, permit
    old = ing.DB_PATH
    ing.DB_PATH = path
    for f in (permit.versions, permit.design_basis, permit.threads, claims.claims, claims.findings):
        f.cache_clear()
    try:
        yield
    finally:
        ing.DB_PATH = old
        for f in (permit.versions, permit.design_basis, permit.threads, claims.claims, claims.findings):
            f.cache_clear()


def snapshot(path: Path, today: dt.date) -> dict:
    from brain import claims, commitments
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    labels = dict(con.execute("SELECT field, label FROM record"))
    current: dict[str, list[dict]] = {}
    for f in con.execute("SELECT * FROM facts WHERE role='current' ORDER BY field, as_of"):
        current.setdefault(f["field"], []).append({"display": f["display"], "doc_id": f["doc_id"], "page_no": f["page_no"],
                                                   "quote": f["quote"], "as_of": f["as_of"]})
    status = dict(con.execute("SELECT field, status FROM record"))
    docs = {r["id"] for r in con.execute("SELECT id FROM documents")}
    con.close()
    with using_db(path):
        verdicts = {f.topic: {"label": f.label, "verdict": f.verdict, "headline": f.headline} for f in claims.findings()}
        deadlines = {i.key: {"label": i.label, "status": i.status, "date": i.date.isoformat(), "doc_id": i.doc_id,
                             "page_no": i.page_no, "quote": i.quote} for i in commitments.dated(today)}
    return {"labels": labels, "current": current, "status": status, "verdicts": verdicts, "deadlines": deadlines, "docs": docs}


def diff(before: dict, after: dict) -> dict:
    out = {"record": [], "verdicts": [], "deadlines": [], "new_docs": sorted(after["docs"] - before["docs"])}
    for fld, label in after["labels"].items():
        a = {(x["display"]) for x in before["current"].get(fld, [])}
        b = after["current"].get(fld, [])
        added = [x for x in b if x["display"] not in a]
        gone = sorted(a - {x["display"] for x in b})
        if added or gone or before["status"].get(fld) != after["status"].get(fld):
            out["record"].append({"field": fld, "label": label, "added": added, "gone": gone,
                                  "status": [before["status"].get(fld), after["status"].get(fld)]})
    for t, v in after["verdicts"].items():
        old = before["verdicts"].get(t)
        if not old or old["verdict"] != v["verdict"] or old["headline"] != v["headline"]:
            out["verdicts"].append({"topic": t, "label": v["label"], "before": old, "after": v})
    for k, v in after["deadlines"].items():
        old = before["deadlines"].get(k)
        if not old or old["status"] != v["status"]:
            out["deadlines"].append({"key": k, "label": v["label"], "before": old and old["status"], "after": v})
    return out


def is_empty(d: dict) -> bool:
    return not (d["record"] or d["verdicts"] or d["deadlines"] or d["new_docs"])


# ---- 4. digest -------------------------------------------------------------------

def markdown(run: dict) -> str:
    d = run.get("diff") or {}
    lines = [f"# Watch digest, {run['ran_at'][:16].replace('T', ' ')}", ""]
    lines.append(f"Mode: {run['mode']}. Sources checked: {', '.join(run['checked'])}.")
    if run.get("problems"):
        lines += ["", "Problems: " + "; ".join(run["problems"])]
    if not run["new"]:
        lines += ["", "Nothing new in the sources since the last snapshot."]
        return "\n".join(lines) + "\n"
    lines += ["", "## New in the sources"] + [f"- {n['source']} ({n['published']})" for n in run["new"]]
    if run.get("summary"):
        lines += ["", "## Summary", run["summary"]]
    if d.get("record"):
        lines += ["", "## Record values that moved"]
        for r in d["record"]:
            for x in r["added"]:
                lines.append(f"- {r['label']}: now {x['display']} ({x['doc_id']}, p. {x['page_no']}: \"{x['quote'][:120]}\")")
            for g in r["gone"]:
                lines.append(f"- {r['label']}: no longer {g}")
            if r["status"][0] != r["status"][1]:
                lines.append(f"- {r['label']}: status {r['status'][0]} -> {r['status'][1]}")
    if d.get("verdicts"):
        lines += ["", "## Consistency check"]
        for v in d["verdicts"]:
            if v["before"] and v["before"]["verdict"] == v["after"]["verdict"]:
                lines.append(f"- {v['label']}: still {v['after']['verdict']}, now \"{v['after']['headline']}\" (was \"{v['before']['headline']}\")")
            else:
                was = v["before"]["verdict"] if v["before"] else "not checked"
                lines.append(f"- {v['label']}: {was} -> {v['after']['verdict']}. {v['after']['headline']}")
    if d.get("deadlines"):
        lines += ["", "## Deadlines"]
        for x in d["deadlines"]:
            lines.append(f"- {x['label']}: {x['before'] or 'new'} -> {x['after']['status']} ({x['after']['date']})")
    if not (d.get("record") or d.get("verdicts") or d.get("deadlines")):
        lines += ["", "New documents arrived, but no record value, consistency verdict or deadline status changed."]
    if run.get("candidate"):
        lines += ["", f"Candidate database: {run['candidate']}. Review, then: python -m brain.watch --promote {run['candidate']}"]
    return "\n".join(lines) + "\n"


def narrate(d: dict, new: list[dict]) -> tuple[str, float, str]:
    """Three plain sentences on what the diff means. Kept only if every number in it is in the diff."""
    from brain.answer import MODEL, PRICE_IN, PRICE_OUT, load_env
    import anthropic
    load_env()
    facts = json.dumps({"new": new, "diff": d}, default=str)[:12000]
    prompt = ("You write the summary line of a monitoring digest for a power plant project. Using only the JSON below, "
              "write at most three plain sentences on what changed and why a project team would care. Do not add "
              "facts, numbers or dates that are not in the JSON. No preamble.\n\n" + facts)
    est = len(prompt) / 3.5 * PRICE_IN + 1500 * PRICE_OUT   # room for the model's thinking plus three sentences
    if est > NARRATE_CAP_USD:
        return "", 0.0, f"skipped: estimated ${est:.3f} is above the ${NARRATE_CAP_USD} cap"
    msg = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    cost = msg.usage.input_tokens * PRICE_IN + msg.usage.output_tokens * PRICE_OUT
    if not text:
        return "", cost, f"dropped: no text returned (stop reason {msg.stop_reason})"
    flat = facts.replace(",", "")
    stray = [n for n in re.findall(r"\d[\d,.]*\d|\d", text) if len(n) > 1 and n.replace(",", "").rstrip(".") not in flat]
    if stray:
        return "", cost, f"dropped: numbers not in the diff: {', '.join(stray)}"
    return text, cost, "kept"


def run(args) -> dict:
    today = dt.date.today()
    started = time.time()
    rec = {"ran_at": dt.datetime.now().isoformat(timespec="seconds"), "mode": "", "checked": [], "new": [],
           "problems": [], "diff": None, "candidate": None, "cost_usd": 0.0}
    if args.replay:
        day = dt.date.fromisoformat(args.replay)
        rec["mode"] = f"replay as of {day}"
        rec["checked"] = ["corpus documents published after " + day.isoformat()]
        base, cand, new = build_replay(day)
        rec["new"] = new
        rec["diff"] = diff(snapshot(base, day + dt.timedelta(days=1)), snapshot(cand, today))
    else:
        rec["mode"] = "dry run" if args.dry_run else "live"
        new = []
        for name, fn in (("ERCOT GIS report list", check_ercot), ("TCEQ pending permit list", check_tceq)):
            rec["checked"].append(name)
            try:
                found = fn(download=not args.dry_run)
            except Exception as e:   # one source failing must not hide the other
                rec["problems"].append(f"{name}: {type(e).__name__}: {e}")
                continue
            for f in found:
                if f.get("problem"):
                    rec["problems"].append(f"{name}: {f['problem']}")
                else:
                    new.append(f)
        rec["new"] = new
        if new and not args.dry_run:
            cand = build_candidate([n["path"] for n in new])
            rec["candidate"] = str(cand.relative_to(ROOT))
            rec["diff"] = diff(snapshot(_live_db(), today), snapshot(cand, today))
    if args.narrate and rec["diff"] and not is_empty(rec["diff"]):
        text, cost, note = narrate(rec["diff"], rec["new"])
        rec["summary"], rec["cost_usd"], rec["summary_note"] = text, round(cost, 4), note
    rec["seconds"] = round(time.time() - started, 1)
    WATCH.mkdir(parents=True, exist_ok=True)
    stamp = rec["ran_at"].replace(":", "").replace("-", "")
    (WATCH / f"digest-{stamp}.json").write_text(json.dumps(rec, indent=1, default=str))
    (WATCH / f"digest-{stamp}.md").write_text(markdown(rec))
    with (WATCH / "runs.jsonl").open("a") as fh:
        fh.write(json.dumps({k: rec[k] for k in ("ran_at", "mode", "problems", "cost_usd", "seconds")}
                            | {"n_new": len(rec["new"]), "changed": bool(rec["diff"] and not is_empty(rec["diff"]))}) + "\n")
    return rec


def promote(path: str) -> None:
    src = (ROOT / path).resolve()
    if not src.exists() or src.parent != WATCH.resolve():
        raise SystemExit(f"{path}: not a candidate under data/watch/")
    backup = _live_db().with_suffix(f".{dt.datetime.now():%Y%m%d%H%M%S}.bak")
    shutil.copyfile(_live_db(), backup)
    shutil.copyfile(src, _live_db())
    print(f"promoted {path} -> data/brain.db (previous copy at {backup.relative_to(ROOT)})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replay", metavar="YYYY-MM-DD")
    ap.add_argument("--narrate", action="store_true")
    ap.add_argument("--promote", metavar="PATH")
    args = ap.parse_args()
    if args.promote:
        promote(args.promote)
        return 0
    rec = run(args)
    print(markdown(rec))
    return 1 if rec["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
