"""Read only web view of the project record and its sources.

  /                      the record card
  /source/<doc>/<page>   the cited page with the quote highlighted
"""

from __future__ import annotations

import json
import re
import sqlite3
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


@app.route("/")
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
                           n_pages=run["n_pages"], latest=latest)


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


@app.route("/ask")
def ask_view():
    from brain.answer import ask
    q = (request.args.get("q") or "").strip()[:300]
    answer = ask(q) if q else None
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


if __name__ == "__main__":
    app.run(port=5055, debug=True)
