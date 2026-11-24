"""Read only web view of the project record and its sources.

  /                      home: pick a role, see what the brain can answer for it
  /record                the record card
  /epc                   the EPC and permitting lens: what the draft permit binds the plant to
  /permit, /permit/<n>   the draft permit's special conditions, their changes and negotiation
  /consistency           what each source says about the same topic, and where they disagree
  /commitments           dated milestones with status, and the clocks the draft permit starts
  /watch                 the watcher agent: latest live check and digests of what moved
  /source/<doc>/<page>   the cited page with the quote highlighted
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from flask import Flask, abort, g, render_template, request
from markupsafe import Markup, escape

from brain.ingest import DB_PATH
from brain.registry import OPTIONAL_KINDS

app = Flask(__name__)

KIND_LABEL = {
    "permit_application": "TCEQ permit file",
    "permit_notice": "TCEQ notice",
    "queue_report": "ERCOT queue report",
    "status_snapshot": "TCEQ status snapshot",
    "company_release": "Company release",
    "press": "Press",
    "analysis": "Analysis",
}
GROUPS = [
    ("What it is", ["capacity_mw", "turbine_fleet", "battery_storage_mw"]),
    ("Who", ["developer", "sponsors", "offtaker", "ppa_term_years", "turbine_suppliers"]),
    ("Where", ["location", "interconnection_point", "queue_entries"]),
    ("When", ["first_power", "fid_status"]),
    ("Permits", ["air_permit_status", "permit_milestones"]),
    ("Environmental", ["co2e_tpy", "nox_controls", "water_source"]),
]


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def chip(doc: sqlite3.Row, locator: str) -> str:
    """Citation chip text, e.g. 'TCEQ permit file, p. 30' or 'ERCOT report, Oct 2026'."""
    import datetime as dt
    if doc["kind"] == "queue_report":
        month_name = doc["title"].rsplit(", ", 1)[-1]
        return f"ERCOT report, {month_name}"
    if doc["kind"] == "status_snapshot":
        return f"TCEQ pending list, {dt.date.fromisoformat(doc['published']).strftime('%b %-d, %Y')}"
    kind = KIND_LABEL.get(doc["kind"], doc["kind"])
    if doc["kind"] == "permit_application":
        kind = "TCEQ application" if doc["id"].endswith("2025-10") else "TCEQ permit file"
    if doc["kind"] in ("press", "analysis", "company_release"):
        kind = doc["publisher"].split(" via ")[0]
    return f"{kind}, {locator}"


@app.template_filter("month")
def month(iso: str) -> str:
    import datetime as dt
    if not iso:
        return ""
    d = dt.date.fromisoformat(iso[:10])
    return d.strftime("%b %-d, %Y")


LANES = [
    {"key": "epc", "title": "EPC and engineering", "who": "Owner's engineer, design leads, commissioning",
     "coverage": "partial", "links": [("epc", "EPC and permitting lens"), ("permit_index", "Draft permit"), ("commitments", "Permit clocks")],
     "asks": ["What NOx limit applies to the simple cycle GE 7HA.02 turbines?", "How long can a simple cycle startup last under the draft permit?"],
     "missing": "RFIs, deviations, vendor delivery dates, drawing revisions"},
    {"key": "permit", "title": "Permitting and environmental", "who": "Air permitting, environmental compliance",
     "coverage": "good", "links": [("permit_index", "Draft permit, condition by condition"), ("commitments", "Deadlines"), ("changes", "What changed")],
     "asks": ["Why did the turbine count change from 44 to 46?", "What CO2e emissions does the permit file report?"],
     "missing": "Modeling files, agency calls, survey reports"},
    {"key": "ercot", "title": "Interconnection", "who": "ERCOT queue, transmission, grid strategy",
     "coverage": "partial", "links": [("commitments", "Projected CODs"), ("changes", "Queue changes"), ("consistency", "Off grid vs grid requests")],
     "asks": ["Where does the plant connect to the grid?", "How big is the battery and has it changed?"],
     "missing": "Study reports, interconnection agreement drafts, PUCT filings"},
    {"key": "comms", "title": "Communications, legal, leadership", "who": "CEO, IR and comms, legal, partner management",
     "coverage": "partial", "links": [("consistency", "Consistency check"), ("commitments", "Deadlines"), ("card", "Project record")],
     "asks": ["Why do the capacity numbers disagree?", "When is first power expected?"],
     "missing": "Board decisions, JV consent rights, contract notice windows"},
    {"key": "commercial", "title": "Commercial and finance", "who": "Offtake, project finance, business development",
     "coverage": "internal", "links": [("consistency", "Ownership and capacity claims"), ("card", "Project record")],
     "asks": ["What is the PPA term?", "Who are the turbine suppliers?"],
     "missing": "The PPA, financial model, lender reports, term sheets"},
    {"key": "ops", "title": "Plant operations", "who": "O&M, controls, HSE",
     "coverage": "partial", "links": [("commitments", "Recurring permit obligations"), ("permit_index", "Monitoring conditions")],
     "asks": ["What emission controls are required on the combustion turbines?", "What sulfur limit applies to fuel for the Solar turbines?"],
     "missing": "LTSA, CMMS history, commissioning records, OEM manuals"},
]


def _change_events() -> list[dict]:
    """The same dated transitions the What changed page lists, newest first."""
    con = db()
    docs = {r["id"]: r for r in con.execute("SELECT * FROM documents")}
    labels = dict(con.execute("SELECT field, label FROM record"))
    by_field = defaultdict(list)
    for f in con.execute("SELECT * FROM facts"):
        f = dict(f)
        f["value"] = json.loads(f["value"])
        f["doc"] = docs[f["doc_id"]]
        f["chip"] = chip(f["doc"], f["locator"])
        f["first"] = {"doc_id": f["first_doc_id"], "page_no": f["first_page_no"], "quote": f["first_quote"],
                      "doc": docs[f["first_doc_id"]], "chip": chip(docs[f["first_doc_id"]], f["first_locator"])}
        by_field[f["field"]].append(f)
    events = []
    for field_name, rows in by_field.items():
        if field_name == "battery_storage_mw":
            continue
        for e in changes_from(rows):
            e["field"] = labels.get(field_name, field_name)
            events.append(e)
    return sorted(events, key=lambda e: e["date"], reverse=True)


def _fact(field_name: str, pick=None) -> dict | None:
    rows = [dict(r) for r in db().execute("SELECT * FROM facts WHERE field=? AND role='current' ORDER BY as_of", (field_name,))]
    if pick:
        rows = [r for r in rows if pick(r)]
    return rows[-1] if rows else None


def _fact_in(doc_id: str) -> dict | None:
    """A verified fact read from this document, so a link to it can highlight a real quote."""
    r = db().execute("SELECT * FROM facts WHERE doc_id=? ORDER BY role='current' DESC, as_of DESC LIMIT 1", (doc_id,)).fetchone()
    return dict(r) if r else None


def _src(f: dict | None) -> dict | None:
    return {"doc_id": f["doc_id"], "page_no": f["page_no"], "quote": f["quote"]} if f else None


def project_header() -> dict:
    """Stat cards, milestone tracks and the latest changes, every item tied to the page it came from."""
    import datetime as dt
    from brain import commitments as M
    today = dt.date.today()
    con = db()
    items = {i.key: i for i in M.dated(today)}
    status = _fact("air_permit_status")
    cap = _fact("capacity_mw", lambda r: r["tier"] == 1)
    cap_rec = con.execute("SELECT status FROM record WHERE field='capacity_mw'").fetchone()
    queue = [(json.loads(r["value"]), dict(r)) for r in con.execute(
        "SELECT * FROM facts WHERE field='queue_entries' AND role='current'")]
    active = sorted([(v, r) for v, r in queue if v.get("status") != "cancelled" and v.get("projected_cod")],
                    key=lambda x: x[0]["projected_cod"])
    first_v, first_r = active[0] if active else (None, None)
    latest_doc = con.execute("SELECT id, title, published, kind FROM documents WHERE tier=1 ORDER BY published DESC LIMIT 1").fetchone()
    events = _change_events()

    def fmt(iso):
        return dt.date.fromisoformat(iso[:10]).strftime("%b %-d, %Y")

    cards = [
        {"label": "Air permit", "value": status["display"].replace(" Application", "") if status else "unknown",
         "sub": f"TCEQ pending list, {fmt(status['as_of'])}" if status else "", "src": _src(status), "tone": "accent"},
        {"label": "Capacity in the permit", "value": cap["display"] if cap else "unknown",
         "sub": "sources disagree" if cap_rec and cap_rec["status"] == "conflict" else "", "src": _src(cap),
         "tone": "conflict" if cap_rec and cap_rec["status"] == "conflict" else "", "link": "consistency"},
        {"label": "Earliest projected COD", "value": fmt(first_v["projected_cod"]) if first_v else "unknown",
         "sub": f"{first_v['name'].replace('Kilby1 ', '')}, {first_v['mw']} MW" if first_v else "", "src": _src(first_r)},
        {"label": "Latest filing", "value": fmt(latest_doc["published"]) if latest_doc else "",
         "sub": KIND_LABEL.get(latest_doc["kind"], "") if latest_doc else "",
         "src": _src(_fact_in(latest_doc["id"])) if latest_doc else None},
        {"label": "Changes in the filings", "value": str(len(events)), "sub": f"latest {fmt(events[0]['date'])}" if events else "",
         "src": None, "link": "changes"},
    ]

    def step(label, when, state, src=None, note=""):
        return {"label": label, "when": when, "state": state, "src": src, "note": note}

    napd = con.execute("SELECT id, published FROM documents WHERE id='tceq-napd'").fetchone()
    meeting = items.get(next((k for k in items if "Public meeting" in items[k].label), ""), None)
    filed = next((i for i in items.values() if "Application filed" in i.label), None)
    issuance = next((i for i in items.values() if "final issuance" in i.label.lower()), None)
    tc = status and "technically complete" in status["display"].lower()
    permit = [
        step("Application filed", fmt(filed.date.isoformat()) if filed else "", "done",
             {"doc_id": filed.doc_id, "page_no": filed.page_no, "quote": filed.quote} if filed else None),
        step("Draft permit noticed", fmt(napd["published"]) if napd else "", "done" if napd else "todo",
             {"doc_id": "tceq-napd", "page_no": 1, "quote": "PRELIMINARY DECISION"} if napd else None),
        step("Public meeting", fmt(meeting.date.isoformat()) if meeting else "", "done" if meeting else "todo",
             {"doc_id": meeting.doc_id, "page_no": meeting.page_no, "quote": meeting.quote} if meeting else None),
        step("Technically complete", f"shown {fmt(status['since'] or status['as_of'])}" if tc else "", "done" if tc else "todo", _src(status)),
        step("Final issuance", f"est. {fmt(issuance.date.isoformat())}" if issuance else "", "late" if issuance and issuance.status == "past_due" else "current",
             {"doc_id": issuance.doc_id, "page_no": issuance.page_no, "quote": issuance.quote} if issuance else None,
             "past the TCEQ estimate" if issuance and issuance.status == "past_due" else ""),
    ]
    phase = (first_v or {}).get("study_phase") or ""
    ercot = [
        step("In the queue", f"{len(active)} active parts", "done", _src(first_r)),
        step("Screening study", "completed" if "SS Completed" in phase else "", "done" if "SS Completed" in phase else "current", _src(first_r)),
        step("Full interconnection study", "started" if "FIS Started" in phase else "", "current" if "FIS Started" in phase else "todo", _src(first_r)),
        step("Interconnection agreement", "none yet" if "No IA" in phase else "", "todo", _src(first_r)),
        step("First COD", fmt(first_v["projected_cod"]) if first_v else "", "todo", _src(first_r), "projected"),
    ]
    ppa = _fact("ppa_term_years")
    fid = items.get("fid")
    fp = items.get("first_power")
    project = [
        step("Microsoft PPA signed", fmt(ppa["as_of"]) if ppa else "", "done" if ppa else "todo", _src(ppa)),
        step("Final investment decision", "target end of 2026" if fid else "", "risk" if fid and fid.status == "at_risk" else "current",
             {"doc_id": fid.doc_id, "page_no": fid.page_no, "quote": fid.quote} if fid else None,
             "a later report says it could slip" if fid and fid.status == "at_risk" else ""),
        step("Construction", "", "todo"),
        step("First power", f"target {fp.date.year}" if fp else "", "todo",
             {"doc_id": fp.doc_id, "page_no": fp.page_no, "quote": fp.quote} if fp else None),
    ]
    recent = []
    for e in events[:3]:
        label, old, new = e["diffs"][0]
        recent.append({"date": fmt(e["date"]), "field": e["field"], "subject": e["subject"], "label": label,
                       "old": old, "new": new, "src": e["new"]["first"]})
    return {"cards": cards, "tracks": [("Air permit", permit), ("ERCOT interconnection", ercot), ("Project", project)],
            "recent": recent}


@app.route("/")
def home():
    con = db()
    run = con.execute("SELECT * FROM ingest_runs ORDER BY ran_at DESC LIMIT 1").fetchone()
    n_docs = con.execute("SELECT count(*) FROM documents").fetchone()[0]
    from brain import claims as C
    from brain import commitments as M
    import datetime as dt
    fs = C.findings()
    items = M.dated(dt.date.today())
    alerts = [f for f in fs if f.verdict in ("disagree", "tension")][:3]
    due = [i for i in items if i.status in ("past_due", "at_risk")]
    return render_template("home.html", lanes=LANES, n_docs=n_docs, n_pages=run["n_pages"], alerts=alerts, due=due,
                           ph=project_header())


@app.route("/record")
def card():
    con = db()
    docs = {r["id"]: r for r in con.execute("SELECT * FROM documents")}
    records = {r["field"]: dict(r) for r in con.execute("SELECT * FROM record ORDER BY position")}
    facts = defaultdict(lambda: {"current": [], "history": []})
    for f in con.execute("SELECT * FROM facts ORDER BY tier, as_of"):
        f = dict(f)
        f["value"] = json.loads(f["value"])
        f["doc"] = docs[f["doc_id"]]
        f["chip"] = chip(f["doc"], f["locator"])
        facts[f["field"]][f["role"]].append(f)
    for fld in facts.values():
        fld["history"].sort(key=lambda f: f["since"] or f["as_of"])
    groups = [(title, [(records[n], facts[n]) for n in names if n in records]) for title, names in GROUPS]
    counts = defaultdict(int)
    for r in records.values():
        counts[r["status"]] += 1
    run = con.execute("SELECT * FROM ingest_runs ORDER BY ran_at DESC LIMIT 1").fetchone()
    latest = con.execute("SELECT max(published) FROM documents WHERE tier = 1").fetchone()[0]
    return render_template("card.html", groups=groups, counts=counts, n_docs=len(docs),
                           n_pages=run["n_pages"], latest=latest, ph=project_header())


QUEUE_KEYS = [("mw", "MW"), ("study_phase", "study phase"), ("technology", "technology"),
              ("fuel", "fuel"), ("projected_cod", "projected COD"), ("status", "status")]


def changes_from(rows: list[dict]) -> list[dict]:
    """Turn fact runs into dated transitions.

    rows: facts for one field, current and history, each with since/until.
    Within a lineage (source_key) runs are ordered by since; each consecutive
    pair is a change, dated by the first document showing the new value and
    bounded by the last document showing the old one.
    """
    lineages = defaultdict(list)
    for f in rows:
        lineages[f["source_key"]].append(f)
    out = []
    for key, runs in lineages.items():
        runs.sort(key=lambda f: (f["seq"], f["since"] or f["as_of"]))
        for old, new in zip(runs, runs[1:]):
            if isinstance(old["value"], dict) and isinstance(new["value"], dict):
                diffs = [(label, old["value"].get(k), new["value"].get(k)) for k, label in QUEUE_KEYS
                         if old["value"].get(k) != new["value"].get(k)]
                subject = f"{new['value'].get('inr')} {new['value'].get('name')}"
            else:
                diffs = [("", old["display"], new["display"])]
                subject = new["basis"] if new["basis"].startswith("ERCOT queue, ") else ""
            out.append({"date": new["since"] or new["as_of"], "last_old": old["until"] or old["as_of"],
                        "subject": subject, "diffs": diffs, "old": old, "new": new, "basis": new["basis"]})
        for f in runs:
            if isinstance(f["value"], dict) and f["value"].get("status") == "cancelled" and f["role"] == "current":
                out.append({"date": f["value"].get("cancel_date") or f["as_of"], "last_old": None,
                            "subject": f"{f['value'].get('inr')} {f['value'].get('name')}",
                            "diffs": [("", None, f"Cancelled from the queue ({f['value'].get('mw')} MW {f['value'].get('fuel') or ''})".replace(" )", ")"))],
                            "old": None, "new": f, "basis": f["basis"]})
    return out


SAMPLE_QUESTIONS = [
    "Why do the capacity numbers disagree?",
    "How big is the battery and has it changed?",
    "When is first power expected?",
    "How much did the GE Vernova turbines cost?",
]


DAILY_CAP_USD = float(os.environ.get("DAILY_CAP_USD", "3"))
PER_IP_PER_HOUR = int(os.environ.get("PER_IP_PER_HOUR", "12"))
CACHE_DAYS = 7
_recent: dict[str, list[float]] = defaultdict(list)


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "?")


def _cached(q: str):
    """Reuse an answer to the same question from the last week, at no cost."""
    from brain.answer import LOG_DB, Answer, Citation
    if not LOG_DB.exists():
        return None
    con = sqlite3.connect(LOG_DB)
    rows = con.execute(
        """SELECT answer_json FROM questions
           WHERE lower(trim(question)) = lower(trim(?)) AND status IN ('answered', 'not_in_documents')
             AND asked_at >= datetime('now', ?) ORDER BY asked_at DESC LIMIT 5""",
        (q, f"-{CACHE_DAYS} days")).fetchall()
    con.close()
    from dataclasses import fields
    a_fields = {f.name for f in fields(Answer)}
    c_fields = {f.name for f in fields(Citation)}
    for (raw,) in rows:
        try:
            d = json.loads(raw)
            cites = []
            for c in d.get("citations", []):
                if "quotes" not in c and "quote" in c:   # rows logged before citations were grouped by page
                    c["quotes"] = [c.pop("quote")]
                cites.append(Citation(**{k: v for k, v in c.items() if k in c_fields}))
            d = {k: v for k, v in d.items() if k in a_fields}
            d["citations"] = cites
            a = Answer(**d)
        except (TypeError, ValueError, KeyError):
            continue   # an older log shape this code can't rebuild: ask fresh instead
        a.cost_usd = 0.0
        return a
    return None


def _spent_today() -> float:
    from brain.answer import LOG_DB
    if not LOG_DB.exists():
        return 0.0
    con = sqlite3.connect(LOG_DB)
    try:
        v = con.execute("SELECT coalesce(sum(cost_usd), 0) FROM questions WHERE asked_at >= date('now')").fetchone()[0]
    finally:
        con.close()
    return float(v)


def guarded_ask(q: str):
    """The public Ask path: cache, per visitor limit, daily budget, then the model."""
    from brain.answer import Answer, ask
    hit = _cached(q)
    if hit:
        hit.note = (hit.note + " " if hit.note else "") + "(Answered earlier; shown from cache.)"
        return hit
    now = time.time()
    ip = _client_ip()
    _recent[ip] = [t for t in _recent[ip] if now - t < 3600]
    if len(_recent[ip]) >= PER_IP_PER_HOUR:
        return Answer(q, "error", note="That's the hourly limit for this demo. Try the sample questions, which are cached.")
    if _spent_today() >= DAILY_CAP_USD:
        return Answer(q, "error", note="Today's demo budget is used up. The sample questions still work from cache.")
    _recent[ip].append(now)
    return ask(q)


@app.route("/ask")
def ask_view():
    q = (request.args.get("q") or "").strip()[:300]
    answer = guarded_ask(q) if q else None
    first = answer.citations[0] if answer and answer.citations else None
    return render_template("ask.html", q=q, a=answer, first=first, samples=SAMPLE_QUESTIONS)


@app.route("/changes")
def changes():
    con = db()
    docs = {r["id"]: r for r in con.execute("SELECT * FROM documents")}
    labels = dict(con.execute("SELECT field, label FROM record"))
    by_field = defaultdict(list)
    for f in con.execute("SELECT * FROM facts"):
        f = dict(f)
        f["value"] = json.loads(f["value"])
        f["doc"] = docs[f["doc_id"]]
        f["chip"] = chip(f["doc"], f["locator"])
        # where this value first appeared, for citing the new side of a change
        f["first"] = {"doc_id": f["first_doc_id"], "page_no": f["first_page_no"], "quote": f["first_quote"],
                      "doc": docs[f["first_doc_id"]], "chip": chip(docs[f["first_doc_id"]], f["first_locator"])}
        by_field[f["field"]].append(f)
    events = []
    for field_name, rows in by_field.items():
        if field_name == "battery_storage_mw":
            continue  # same change as the 28INR0457 queue entry; shown once
        for e in changes_from(rows):
            e["field"] = labels.get(field_name, field_name)
            events.append(e)
    events.sort(key=lambda e: e["date"], reverse=True)
    return render_template("changes.html", events=events)


def highlight(text: str, quote: str) -> tuple[Markup, bool]:
    """Escape the page and wrap each quoted part in <mark>, tolerant of whitespace."""
    parts = [p for p in re.split(r"\s*\.\.\.\s*", quote) if p.strip()]
    spans = []
    for p in parts:
        rx = r"\s+".join(re.escape(w) for w in p.split())
        m = re.search(rx, text)
        if m:
            spans.append((m.start(), m.end()))
    if not spans:
        return Markup(escape(text)), False
    spans.sort()
    out, pos = [], 0
    for a, b in spans:
        if a < pos:
            continue
        out.append(escape(text[pos:a]))
        out.append(Markup('<mark id="q">') + escape(text[a:b]) + Markup("</mark>"))
        pos = b
    out.append(escape(text[pos:]))
    return Markup("").join(out), True


EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+\.)+[a-z]{2,}\b", re.I)


def _mask(m: re.Match) -> str:
    """Keep agency addresses (public officials); mask everyone else's mailbox."""
    addr = m.group(0)
    user, _, domain = addr.partition("@")
    if domain.lower().endswith((".gov", ".texas.gov")):
        return addr
    return f"[email withheld]@{domain}" if domain.lower() not in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com") else "[email withheld]"


@app.route("/source/<doc_id>/<int:page_no>")
def source(doc_id: str, page_no: int):
    con = db()
    doc = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    page = con.execute("SELECT * FROM pages WHERE doc_id=? AND page_no=?", (doc_id, page_no)).fetchone()
    if not doc or not page:
        abort(404)
    quote = request.args.get("q", "")
    redistributable = doc["kind"] not in OPTIONAL_KINDS
    text = EMAIL.sub(_mask, page["text"])
    body, found = highlight(text, quote) if redistributable else (None, None)
    original = doc["url"] + (f"#page={page_no}" if doc["path"].endswith(".pdf") else "")
    return render_template("source.html", doc=doc, page=page, quote=quote, body=body, found=found,
                           embed=request.args.get("embed") == "1",
                           redistributable=redistributable, original=original,
                           kind=KIND_LABEL.get(doc["kind"], doc["kind"]))


EVAL_PATH = Path(__file__).resolve().parent.parent / "data" / "eval.json"


def _pct(x):
    return "n/a" if x is None else f"{int(x * 100 + 0.5)}%"   # round half up: 12.5% shows as 13%


@app.route("/eval")
def eval_view():
    data = json.loads(EVAL_PATH.read_text())
    runs = data["runs"]
    for r in runs:
        s = r["summary"]
        r["rows"] = [
            ("Answer accuracy", s["answer_accuracy"], "answerable and change questions where every expected fact is in the answer"),
            ("Citation accuracy", s["citation_accuracy"], "answered questions with a citation to an allowed document whose page holds the fact"),
            ("Refusals", s["refusal_recall"], "questions the documents can't answer that were declined"),
            ("False refusals", s["false_refusal_rate"], "answerable questions that were declined or withheld (lower is better)"),
        ]
    heldout = next(r for r in runs if r["split"] == "heldout")
    latest_of = {r["split"]: r for r in runs}   # the last run listed for each split
    table = []
    for cid, c in data["cases"].items():
        run = latest_of.get(c["split"])
        if run is None:
            continue
        res = run["cases"].get(cid)
        if not res:
            continue
        g = res["grade"]
        if c["kind"] == "unanswerable":
            verdict = "pass" if g.get("declined") else "fail"
        else:
            verdict = "pass" if g.get("answer_correct") and g.get("citation_correct") else (
                "declined" if g.get("declined") else "fail")
        history = [(r["label"], r["cases"].get(cid, {}).get("grade", {})) for r in runs if r["split"] == c["split"]]
        table.append({**c, **res, "verdict": verdict, "run": run["label"], "history": history})
    total_cost = sum(r["summary"]["cost_usd_total"] for r in runs)
    return render_template("eval.html", runs=runs, heldout=heldout, table=table, pct=_pct, total_cost=total_cost)


# ---- EPC and permitting lens ----------------------------------------------------

EPC_QUESTIONS = [
    "What NOx limit applies to the simple cycle GE 7HA.02 turbines?",
    "How long can a simple cycle startup last under the draft permit?",
    "What sulfur limit applies to fuel for the Solar turbines?",
    "Why did the turbine count change from 44 to 46?",
    "What emission controls are required on the combustion turbines?",
    "Where does the plant connect to the grid?",
]

# Questions an EPC team asks every week that the public record cannot answer.
NEEDS_INTERNAL = [
    ("Which design basis deviations has the EPC requested, and which did we approve?", "Deviation and waiver log, design basis memo"),
    ("What did GE Vernova commit to for delivery of each 7HA.02 unit, and has any date slipped against the P6 need date?", "Turbine supply agreement, expediting reports, Primavera P6"),
    ("Which switchyard RFIs are open, and who owns them?", "Procore or Aconex RFI log"),
    ("Which single line diagram revision is current, and what changed from the last one?", "Document control register, drawing revisions"),
    ("Does the GSU factory test report match the impedance we specified?", "Transformer specs, factory acceptance test reports"),
    ("Has the EPC given timely notice of delay under the contract, and what are the LD caps?", "EPC contract, notice and correspondence log"),
]

EPC_RECORD_FIELDS = ["turbine_fleet", "capacity_mw", "interconnection_point", "battery_storage_mw",
                     "air_permit_status", "permit_milestones", "nox_controls", "water_source"]


def _facts_for(names: list[str]) -> list[tuple[dict, dict]]:
    con = db()
    docs = {r["id"]: r for r in con.execute("SELECT * FROM documents")}
    records = {r["field"]: dict(r) for r in con.execute("SELECT * FROM record")}
    facts = defaultdict(lambda: {"current": [], "history": []})
    for f in con.execute("SELECT * FROM facts ORDER BY tier, as_of"):
        f = dict(f)
        if f["field"] not in names:
            continue
        f["value"] = json.loads(f["value"])
        f["doc"] = docs[f["doc_id"]]
        f["chip"] = chip(f["doc"], f["locator"])
        facts[f["field"]][f["role"]].append(f)
    return [(records[n], facts[n]) for n in names if n in records]


def _lead_quote(text: str, words: int = 10) -> str:
    return " ".join(text.split()[:words])


@app.route("/epc")
def epc():
    from brain import permit as P
    basis = P.design_basis()
    eq = basis["equipment"]
    total_units = sum(v.extra["count"] for v in eq)
    total_mw = sum(v.extra["count"] * float(v.extra["mw"].replace(",", "")) for v in eq)
    groups = []
    for v in basis["limits"]:
        if not groups or groups[-1]["name"] != v.extra["group"]:
            groups.append({"name": v.extra["group"], "cells": {}})
        groups[-1]["cells"][v.extra["pollutant"]] = v
    changed = [n for n, c in P.changed_numbers().items() if c]
    return render_template(
        "epc.html", basis=basis, total_units=total_units, total_mw=total_mw, limit_groups=groups,
        threads=P.threads(), changed=changed, first=P.first(), latest=P.latest(), versions=P.versions(),
        record=_facts_for(EPC_RECORD_FIELDS), questions=EPC_QUESTIONS, needs=NEEDS_INTERNAL,
        doc_id=P.DOC_ID, topic_label=P.topic_label, cond_quote=lambda n: _lead_quote(P.latest().conditions[n].text))


@app.route("/permit")
def permit_index():
    from brain import permit as P
    latest = P.latest()
    changed = P.changed_numbers()
    by_cond = defaultdict(list)
    for t in P.threads():
        for n in t.conditions:
            by_cond[n].append(t)
    topic = request.args.get("topic", "")
    keys = [k for k, *_ in P.TOPICS] + ["other"]
    sections = []
    for k in keys:
        rows = [c for c in latest.conditions.values() if c.topic == k]
        if rows and (not topic or topic == k):
            sections.append((k, P.topic_label(k), k in P.epc_topics(), rows))
    return render_template("permit.html", latest=latest, first=P.first(), versions=P.versions(), sections=sections,
                           changed=changed, by_cond=by_cond, topic=topic, topics=[(k, P.topic_label(k)) for k in keys],
                           doc_id=P.DOC_ID, lead=_lead_quote)


@app.route("/permit/<int:number>")
def permit_condition(number: int):
    from brain import permit as P
    latest = P.latest()
    cond = latest.conditions.get(number)
    if cond is None:
        abort(404)
    nums = sorted(latest.conditions)
    i = nums.index(number)
    return render_template("permit_condition.html", c=cond, latest=latest, first=P.first(), net=P.net_change(number),
                           story=P.story(number),
                           history=P.history(number), threads=P.threads_for(number), doc_id=P.DOC_ID, lead=_lead_quote,
                           topic_label=P.topic_label, prev=nums[i - 1] if i else None, next=nums[i + 1] if i + 1 < len(nums) else None)


@app.route("/consistency")
def consistency():
    from brain import claims as C
    con = db()
    docs = {r["id"]: r for r in con.execute("SELECT * FROM documents")}
    locs = {}
    found, misses = C.claims()
    for c in found:
        loc = con.execute("SELECT locator FROM pages WHERE doc_id=? AND page_no=?", (c.doc_id, c.page_no)).fetchone()
        locs[(c.doc_id, c.page_no)] = chip(docs[c.doc_id], loc["locator"] if loc else f"p. {c.page_no}")
    fs = C.findings()
    counts = defaultdict(int)
    for f in fs:
        counts[f.verdict] += 1
    grid = _consistency_grid(fs)
    return render_template("consistency.html", findings=fs, counts=counts, locs=locs, misses=misses, grid=grid,
                           n_sources=len({c.doc_id for c in found}), n_claims=len(found))


GRID_SHORT = {  # short cell text for category claims
    "not_connected_initially": "Off grid at first", "behind_meter": "Behind the meter", "direct_to_customer": "Direct to Microsoft",
    "grid_two_way": "To and from the grid", "ercot_queue": "Grid requests filed", "chevron_wholly_owned": "Wholly owned by Chevron",
    "jv_50_50": "50/50 with Chevron", "brackish": "Brackish groundwater", "chevron_econ": "$10B+ tax, ~2,000 jobs",
}


def _consistency_grid(fs) -> dict:
    """Sources as rows, topics as columns. A cell is colored against the topic's most common value."""
    from collections import Counter
    from brain import claims as C
    party_order = {"Regulatory filing": 0, "ERCOT queue": 1, "Company": 2, "Press": 3, "Analysis": 4}
    sources: dict[str, dict] = {}
    cols = []
    for f in sorted(fs, key=lambda f: list(C_TOPIC_ORDER).index(f.topic) if f.topic in C_TOPIC_ORDER else 99):
        common = Counter(c.value for c in f.claims if not c.note.startswith(("upper bound", "a floor"))).most_common(1)
        majority = common[0][0] if common else None
        cols.append({"topic": f.topic, "label": f.label, "verdict": f.verdict})
        for c in f.claims:
            row = sources.setdefault(c.source, {"source": c.source, "party": c.party, "cells": {}})
            if f.verdict in ("consistent", "single"):
                tone = "agree" if f.verdict == "consistent" else ""
            elif f.topic == "ownership":
                tone = "earlier" if c.value != majority else "agree"
            elif f.topic == "grid":   # grouped by meaning: off grid wordings agree with each other
                tone = "agree" if c.value in C.GRID_ISLAND else "differ"
            elif c.value == majority:
                tone = "agree"
            else:
                tone = "differ"
            text = GRID_SHORT.get(c.value, c.display) if isinstance(c.value, str) else c.display
            if f.topic == "first_power" and c.extra.get("date"):
                text = c.display.replace(", projected COD", "")
            row["cells"].setdefault(f.topic, []).append({"text": text, "tone": tone, "date": c.date, "c": c})
    rows = sorted(sources.values(), key=lambda r: (party_order.get(r["party"], 9), -len(r["cells"]), r["source"]))
    return {"cols": cols, "rows": rows}


C_TOPIC_ORDER = ["capacity", "first_power", "grid", "ownership", "co2", "water", "economy"]


@app.route("/commitments")
def commitments():
    import datetime as dt
    from brain import commitments as M
    from brain import permit as P
    today = dt.date.today()
    items = M.dated(today)
    con = db()
    docs = {r["id"]: r for r in con.execute("SELECT * FROM documents")}
    chips = {}
    for i in items:
        loc = con.execute("SELECT locator FROM pages WHERE doc_id=? AND page_no=?", (i.doc_id, i.page_no)).fetchone()
        chips[i.key] = chip(docs[i.doc_id], loc["locator"] if loc else f"p. {i.page_no}")
    trig, misses = M.triggered()
    groups = [(g, [t for t in trig if t.group == g]) for g in M.GROUPS]
    counts = defaultdict(int)
    for i in items:
        counts[i.status] += 1
    nxt = next((i for i in items if i.days >= 0), None)
    return render_template("commitments.html", items=items, chips=chips, groups=groups, misses=misses, today=today,
                           counts=counts, nxt=nxt, doc_id=P.DOC_ID, latest=P.latest(), n_trig=len(trig))


WATCH_DIR = Path(__file__).resolve().parent.parent / "data" / "watch"


def _digests() -> list[dict]:
    out = []
    for p in sorted(WATCH_DIR.glob("digest-*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text()))
        except ValueError:
            continue
    return out


@app.route("/watch")
def watch():
    ds = _digests()
    live = next((d for d in ds if not d["mode"].startswith("replay")), None)
    replay = next((d for d in ds if d["mode"].startswith("replay") and d.get("summary")), None) or \
        next((d for d in ds if d["mode"].startswith("replay")), None)
    runs = []
    log = WATCH_DIR / "runs.jsonl"
    if log.exists():
        runs = [json.loads(l) for l in log.read_text().splitlines() if l.strip()][-12:][::-1]
    return render_template("watch.html", live=live, replay=replay, runs=runs)


@app.route("/healthz")
def healthz():
    return {"ok": True, "pages": db().execute("SELECT count(*) FROM pages").fetchone()[0]}


if __name__ == "__main__":
    app.run(port=5055, debug=True)
