"""Run and grade the Project Brain eval.

  python -m eval.run --check            verify the labels against the corpus (free)
  python -m eval.run --selftest         oracle and null answers through the grader (free)
  python -m eval.run --only a01,u03     run a few cases (paid)
  python -m eval.run                    full run (paid)

Grading is programmatic, three scores kept separate and never averaged:
  answer accuracy    answerable + change cases: answered, and every fact group present
  citation accuracy  of those answered: a citation from an allowed document whose
                     page or quote contains one of the expected facts
  refusal calibration  unanswerable declined (recall) vs answerable declined
                     (false refusals); "withheld" counts as declined
Output follows the hillclimb layout: .claude/hillclimb/answer/<variant>/results.jsonl,
traces/<id>_rep0.json, errors.jsonl.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import yaml

from brain.answer import SYSTEM, Answer, Citation, ask
from brain.ingest import DB_PATH
from brain.registry import ROOT

CASES = ROOT / "eval" / "cases.yaml"
OUT = ROOT / ".claude" / "hillclimb" / "answer"
SETS = {  # name: (cases file, output folder under .claude/hillclimb/)
    "tuning": ("cases.yaml", "answer"),
    "heldout": ("heldout.yaml", "answer-heldout"),
    "lens": ("lens.yaml", "answer-lens"),
    "lens-heldout": ("lens-heldout.yaml", "answer-lens-heldout"),
}


def load_cases(path: Path | None = None) -> list[dict]:
    return yaml.safe_load((path or CASES).read_text())["cases"]


def flat(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(",", "")).lower()


def has(alt: str, text: str) -> bool:
    """Numbers and short codes match as whole tokens ("20" must not match "2026"); words match as prefixes ("cancel" matches "cancelled")."""
    a = flat(alt)
    if re.fullmatch(r"[\d./-]+|[a-z]{1,3}", a):
        return re.search(r"(?<![\w.])" + re.escape(a) + r"(?![\w])", text) is not None
    return a in text


def page_text(con: sqlite3.Connection, doc_id: str, page_no: int) -> str:
    row = con.execute("SELECT text FROM pages WHERE doc_id=? AND page_no=?", (doc_id, page_no)).fetchone()
    return row[0] if row else ""


# ---------- grading ----------

def grade(case: dict, a: Answer, con: sqlite3.Connection) -> dict:
    declined = a.status in ("not_in_documents", "withheld")
    text = flat(" ".join(s["text"] for s in a.sentences))
    if case["kind"] == "unanswerable":
        return {"declined": 1.0 if declined else 0.0}
    groups = case["facts"]
    facts_ok = a.status == "answered" and all(any(has(alt, text) for alt in g) for g in groups)
    cite_ok = False
    for c in a.citations:
        if not any(c.doc_id.startswith(p) for p in case["docs"]):
            continue
        hay = flat(page_text(con, c.doc_id, c.page_no) + " " + " ".join(c.quotes))
        if any(has(alt, hay) for g in groups for alt in g):
            cite_ok = True
            break
    return {
        "answer_correct": 1.0 if facts_ok else 0.0,
        "citation_correct": (1.0 if cite_ok else 0.0) if a.status == "answered" else None,
        "declined": 1.0 if declined else 0.0,
    }


def summarize(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["kind"] != "unanswerable"]
    una = [r for r in rows if r["kind"] == "unanswerable"]
    answered = [r for r in ans if r["grade"].get("citation_correct") is not None]

    def rate(xs, key):
        vals = [x["grade"][key] for x in xs if x["grade"].get(key) is not None]
        if not vals:
            return None, 0, 0.0
        p = sum(vals) / len(vals)
        half = 1.96 * math.sqrt(p * (1 - p) / len(vals)) if len(vals) > 1 else 0.0
        return p, len(vals), half

    out = {}
    for name, xs, key in [("answer_accuracy", ans, "answer_correct"),
                          ("citation_accuracy", answered, "citation_correct"),
                          ("refusal_recall", una, "declined"),
                          ("false_refusal_rate", ans, "declined")]:
        p, n, half = rate(xs, key)
        out[name] = {"value": p, "n": n, "ci95": half}
    costs = [r["cost_usd"] for r in rows]
    lat = sorted(r["latency_s"] for r in rows)
    out["cost_usd_total"] = round(sum(costs), 4)
    out["cost_usd_per_question"] = round(sum(costs) / len(costs), 4) if costs else None
    out["latency_s_median"] = lat[len(lat) // 2] if lat else None
    return out


# ---------- checks that cost nothing ----------

def check_labels(path: Path | None = None) -> int:
    con = sqlite3.connect(DB_PATH)
    problems = 0
    for c in load_cases(path):
        if c["kind"] == "unanswerable":
            for term in c.get("absent", []):
                n = con.execute("SELECT count(*) FROM pages WHERE text LIKE ?", (f"%{term}%",)).fetchone()[0]
                if n:
                    print(f"{c['id']}: '{term}' appears on {n} pages, so the label may be wrong")
                    problems += 1
            continue
        for g in c["facts"]:
            found = False
            for prefix in c["docs"]:
                for (t,) in con.execute("SELECT text FROM pages WHERE doc_id LIKE ?", (prefix + "%",)):
                    if any(has(alt, flat(t)) for alt in g):
                        found = True
                        break
                if found:
                    break
            if not found:
                print(f"{c['id']}: no page in {c['docs']} contains any of {g}")
                problems += 1
    cases = load_cases(path)
    kinds = {k: sum(1 for c in cases if c["kind"] == k) for k in ("answerable", "change", "unanswerable")}
    print(f"{len(cases)} cases {kinds}; {problems} label problems")
    return problems


def selftest(path: Path | None = None) -> int:
    """Oracle answers must score 100%, null answers 0%."""
    con = sqlite3.connect(DB_PATH)
    bad = 0
    for c in load_cases(path):
        if c["kind"] == "unanswerable":
            ok = grade(c, Answer(c["question"], "not_in_documents"), con)["declined"] == 1.0
            null = grade(c, Answer(c["question"], "answered", sentences=[{"text": "It is 42.", "cites": []}]), con)["declined"] == 0.0
        else:
            alt = [g[0] for g in c["facts"]]
            row = None
            for prefix in c["docs"]:
                row = con.execute(
                    "SELECT doc_id, page_no FROM pages WHERE doc_id LIKE ? AND " +
                    " AND ".join(["replace(lower(text), ',', '') LIKE ?"] * 1), (prefix + "%", f"%{flat(alt[0])}%")).fetchone()
                if row:
                    break
            cit = [Citation(1, "P1", row[0], row[1], "", "", 1, [alt[0]])] if row else []
            g = grade(c, Answer(c["question"], "answered", sentences=[{"text": " and ".join(alt), "cites": [1]}],
                                citations=cit), con)
            ok = g["answer_correct"] == 1.0 and g["citation_correct"] == 1.0
            gn = grade(c, Answer(c["question"], "answered", sentences=[{"text": "", "cites": []}]), con)
            null = gn["answer_correct"] == 0.0 and gn["citation_correct"] == 0.0
        if not (ok and null):
            print(f"{c['id']}: oracle ok={ok} null ok={null}")
            bad += 1
    print(f"selftest: {bad} failures")
    return bad


# ---------- run ----------

def run_case(case: dict) -> tuple[dict, list]:
    t0 = time.time()
    a = ask(case["question"], log=False)
    latency = round(time.time() - t0, 1)
    con = sqlite3.connect(DB_PATH)
    g = grade(case, a, con)
    con.close()
    row = {
        "prompt_id": case["id"], "prompt": case["question"], "kind": case["kind"],
        "tags": [case["kind"]], "status": "ok", "stop_reason": a.status,
        "grade": {k: v for k, v in g.items() if v is not None},
        "answer_status": a.status, "model": a.model,
        "usage": {"input_tokens": a.input_tokens, "output_tokens": a.output_tokens},
        "cost_usd": a.cost_usd, "latency_s": latency, "n_citations": len(a.citations),
        "problems": a.problems,
    }
    answer_text = " ".join(s["text"] + "".join(f"[{n}]" for n in s["cites"]) for s in a.sentences)
    cites = "\n".join(f"[{c.n}] {c.doc_id} {c.locator}: " + " | ".join(c.quotes) for c in a.citations)
    trace = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": case["question"]},
        {"role": "assistant", "content": f"[{a.status}] {answer_text or a.note}\n\n{cites}".strip()},
    ]
    if a.draft:
        trace.append({"role": "tool_result", "content": "Withheld draft: " + json.dumps(a.draft) +
                      "\nProblems: " + json.dumps(a.problems)})
    return row, trace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--set", default="tuning", choices=list(SETS),
                    help="heldout runs eval/heldout.yaml into .claude/hillclimb/answer-heldout/; lens and lens-heldout cover the lens pages")
    args = ap.parse_args()
    path = ROOT / "eval" / SETS[args.set][0]
    if args.check:
        return 1 if check_labels(path) else 0
    if args.selftest:
        return 1 if selftest(path) else 0

    cases = load_cases(path)
    if args.only:
        wanted = [w.strip() for w in args.only.split(",")]
        cases = [c for c in cases if any(c["id"].startswith(w) for w in wanted)]
    out = OUT.parent / SETS[args.set][1] / args.variant
    (out / "traces").mkdir(parents=True, exist_ok=True)
    done = set()
    results = out / "results.jsonl"
    if results.exists():
        done = {json.loads(l)["prompt_id"] for l in results.read_text().splitlines() if l.strip()}
    todo = [c for c in cases if c["id"] not in done]
    print(f"running {len(todo)} cases ({len(done)} already done) on {args.workers} workers")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run_case, c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                row, trace = fut.result(timeout=300)
            except Exception as e:   # harness failure: goes to errors.jsonl, not results
                with (out / "errors.jsonl").open("a") as fh:
                    fh.write(json.dumps({"prompt_id": c["id"], "error": repr(e)[:500]}) + "\n")
                print(f"  ERROR {c['id']}: {e!r}"[:200])
                continue
            with results.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            (out / "traces" / f"{c['id']}_rep0.json").write_text(json.dumps(trace, indent=2))
            print(f"  {c['id']:<28} {row['answer_status']:<17} {row['grade']}  ${row['cost_usd']:.3f}")
    rows = [json.loads(l) for l in results.read_text().splitlines() if l.strip()]
    summary = summarize(rows)
    summary["wall_clock_s"] = round(time.time() - t0, 1)
    (out / "summary_scores.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
