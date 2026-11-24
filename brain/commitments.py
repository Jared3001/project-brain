"""Commitments and deadlines: what the project has promised, when it is due, and where it stands.

Two kinds of entries:

  dated      milestones with a calendar date or year, from the record's verified
             facts (permit milestones, ERCOT projected COD, FID and first power
             statements). Status is computed against a given day.
  triggered  obligations in the draft permit that start a clock on an event
             (initial startup, sampling, a leak) or recur (quarterly, annually).
             Each is read with a pattern anchored on the condition's wording and
             cited to the page it sits on.

Statuses are plain rules, not judgment: a past event is done; an estimate whose
date has passed while the latest status snapshot shows no change is past due; a
target contradicted by a later source is flagged at risk.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass, field


@dataclass
class Dated:
    key: str
    label: str
    date: dt.date
    precision: str            # day | year
    kind: str                 # event | estimate | target | projection
    role: str
    doc_id: str
    page_no: int
    quote: str
    source: str
    stated: str               # when the source said it
    note: str = ""
    status: str = ""          # done | past_due | at_risk | due_soon | upcoming
    days: int = 0             # days from today (negative = in the past)


@dataclass
class Triggered:
    condition: int
    group: str
    window: str
    obligation: str
    role: str
    page_no: int
    quote: str


ROLE = {"permit": "Permitting", "dev": "Development and leadership", "ercot": "Interconnection",
        "epc": "EPC and commissioning", "ops": "Plant operations", "env": "Environmental compliance"}


def _connect():
    from brain.ingest import DB_PATH
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _facts(con, field_name):
    return [dict(r) for r in con.execute(
        "SELECT f.*, d.publisher FROM facts f JOIN documents d ON d.id = f.doc_id WHERE field=? AND role='current' ORDER BY as_of",
        (field_name,))]


MILESTONE = re.compile(r"^(?P<date>[^:]+):\s*(?P<what>.+)$")


def _parse_date(s: str) -> dt.date | None:
    s = re.sub(r"^[A-Z][a-z]+day, ", "", s.strip())
    for fmt in ("%B %d, %Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def dated(today: dt.date | None = None) -> list[Dated]:
    today = today or dt.date.today()
    con = _connect()
    try:
        out: list[Dated] = []
        status_now = _facts(con, "air_permit_status")
        latest_status = status_now[-1] if status_now else None

        for f in _facts(con, "permit_milestones"):
            m = MILESTONE.match(f["display"])
            d = _parse_date(m.group("date")) if m else None
            if not d:
                continue
            what = m.group("what")
            kind = "estimate" if "estimate" in what.lower() else "event"
            label = re.sub(r"\s*\(TCEQ estimate[^)]*\)", "", what)
            label = label[0].upper() + label[1:]
            item = Dated(f"permit:{d}", f"Air permit: {label}", d, "day", kind, ROLE["permit"], f["doc_id"], f["page_no"],
                         f["quote"], f["publisher"].split(" via ")[0], f["as_of"], "TCEQ estimate" if kind == "estimate" else "")
            if kind == "estimate" and "technically complete" in what.lower() and latest_status \
                    and "technically complete" in latest_status["display"].lower():
                item.kind = "event_reached"
                item.note = (f"TCEQ estimate. Reached: {latest_status['display']} first appears on the TCEQ pending list "
                             f"snapshot of {_fmt(latest_status['since'] or latest_status['as_of'])}; snapshots are sparse, so the exact day is unknown.")
            if kind == "estimate" and "issuance" in what.lower() and latest_status:
                item.note = (f"TCEQ estimate. The latest TCEQ pending list ({_fmt(latest_status['as_of'])}) "
                             f"still shows: {latest_status['display']}.")
            out.append(item)

        for f in _facts(con, "fid_status"):
            if "end of 2026" in f["display"]:
                out.append(Dated("fid", "Final investment decision", dt.date(2026, 12, 31), "year", "target", ROLE["dev"],
                                 f["doc_id"], f["page_no"], f["quote"], f["publisher"], f["as_of"], "Expected by the end of 2026"))
        risk = [f for f in _facts(con, "fid_status") if "delay" in f["display"].lower()]
        for item in out:
            if item.key == "fid" and risk:
                r = risk[-1]
                item.note += f". {r['publisher']} ({_fmt(r['as_of'])}): {r['display']}."
                item.extra_risk = r  # type: ignore[attr-defined]

        for f in _facts(con, "first_power"):
            v = json.loads(f["value"])
            if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d{4}", v)):
                y = int(v)
                out.append(Dated("first_power", "First power", dt.date(y, 12, 31), "year", "target", ROLE["dev"],
                                 f["doc_id"], f["page_no"], f["quote"], f["publisher"], f["as_of"], f"Public target: {y}"))

        for f in _facts(con, "queue_entries"):
            v = json.loads(f["value"])
            if v.get("status") == "cancelled" or not v.get("projected_cod"):
                continue
            d = dt.date.fromisoformat(v["projected_cod"])
            phase = v.get("study_phase") or ""
            note = f"{v['mw']} MW. Study phase: {phase}." + (" No interconnection agreement yet." if "No IA" in phase else "")
            out.append(Dated(f"cod:{v['inr']}", f"Projected COD, {v['name'].replace('Kilby1 ', '')}", d, "day", "projection",
                             ROLE["ercot"], f["doc_id"], f["page_no"], f["quote"], "ERCOT queue", f["as_of"], note))
    finally:
        con.close()

    for item in out:
        item.days = (item.date - today).days
        if item.kind == "event_reached":
            item.status = "done"
        elif item.kind == "event":
            item.status = "done" if item.days <= 0 else "upcoming"
        elif item.kind == "estimate" and item.days < 0:
            item.status = "past_due"
        elif getattr(item, "extra_risk", None) is not None:
            item.status = "at_risk"
        elif 0 <= item.days <= 90:
            item.status = "due_soon"
        elif item.days < 0:
            item.status = "past_due"
        else:
            item.status = "upcoming"
    # first power has a year only; the earliest ERCOT block lands before it, which the page points out
    return sorted(out, key=lambda i: i.date)


def _fmt(iso: str) -> str:
    return dt.date.fromisoformat(iso[:10]).strftime("%b %-d, %Y")


# ---- obligations the draft permit triggers -------------------------------------

# (condition, group, window, obligation, role, pattern anchored on the condition text)
TRIGGERED = [
    (27, "After initial startup", "60 days after max operating rate, and within 180 days of startup",
     "Stack sampling on each CTG and the boiler", "epc",
     r"Sampling shall occur within 60 days after achieving the maximum operating rate at which the CTG or boiler will be operated, but no later than 180 days after initial start-up of the unit"),
    (10, "After initial startup", "Within 180 days of startup",
     "Performance testing of each emission source", "epc",
     r"Upon initial startup of each emission source, performance testing shall occur within 180 days following startup"),
    (10, "After initial startup", "Within 180 days after commissioning ends",
     "Apply to alter the permit to remove the CTG commissioning conditions", "env",
     r"within 180 days following the completion of the commissioning period for the CTGs to remove the special conditions related to the CTG commissioning period"),
    (27, "Testing and notices", "At least 45 days before sampling",
     "Notify the TCEQ Regional Office of stack sampling", "env",
     r"The appropriate TCEQ Regional Office shall be notified not less than 45 days prior to sampling"),
    (27, "Testing and notices", "Within 60 days after sampling",
     "Send the final sampling report to TCEQ", "env",
     r"Copies of the final sampling report shall be forwarded to the offices below within 60 days after sampling is completed"),
    (27, "Testing and notices", "Within 120 days of a new operating condition",
     "Resample if the units run above the tested conditions", "ops",
     r"stack sampling shall be performed at the new operating conditions within 120 days"),
    (28, "Testing and notices", "At least 30 days before any RATA",
     "Notify the TCEQ Regional Office of a relative accuracy test audit", "env",
     r"The appropriate TCEQ Regional Office shall be notified at least 30 days prior to any required relative accuracy test audit \(RATA\)"),
    (28, "Recurring", "Quarterly",
     "Cylinder gas audits on each CEMS", "ops",
     r"Each monitor shall be quality-assured at least quarterly using Cylinder Gas Audits \(CGA\)"),
    (11, "Recurring", "Quarterly",
     "Visible emission observations on every stack", "ops",
     r"Visible emission observations shall be performed and recorded quarterly for each CTG stack"),
    (8, "Recurring", "Every 6 months",
     "Sample the natural gas for total sulfur and heating value", "ops",
     r"The natural gas shall be sampled at least every 6 months to determine total sulfur and net heating value"),
    (31, "Recurring", "Annually",
     "Revalidate the estimated emissions from inherently low emitting MSS activities", "env",
     r"The estimated emissions from the activities listed in Attachment A must be revalidated annually"),
    (29, "Recurring", "Annually",
     "Calibrate the oxidation catalyst temperature monitors", "ops",
     r"The temperature monitor shall be installed, calibrated, or have a calibration check performed at least annually"),
    (24, "When a leak is found", "Within 1 hour",
     "Isolate, contain or otherwise act on a detected ammonia leak", "ops",
     r"Immediately, but no later than one hour upon detection of a leak"),
    (25, "When a leak is found", "Within 15 days",
     "Repair the leaking component", "ops",
     r"A leaking component shall be repaired as soon as practicable, but no later than 15 days after the leak is found"),
]
GROUPS = ["After initial startup", "Testing and notices", "Recurring", "When a leak is found"]


def triggered() -> tuple[list[Triggered], list[str]]:
    from brain import permit as P
    v = P.latest()
    out, misses = [], []
    for n, group, window, what, role, pat in TRIGGERED:
        c = v.conditions.get(n)
        m = re.search(pat, c.text) if c else None
        if not m:
            misses.append(f"SC {n}: {what}")
            continue
        page, quote = P._page_of(v, c, " ".join(m.group(0).split()))
        out.append(Triggered(n, group, window, what, ROLE[role], page, quote))
    return out, misses
