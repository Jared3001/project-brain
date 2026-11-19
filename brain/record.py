"""Extract the project record from ingested pages.

Deterministic, label anchored extraction: each fact comes from a regex anchored
on the wording of a specific document, never from inference. Every fact is
verified before it is stored: its quote must appear on the cited page, and its
displayed number must appear in its quote. A fact that fails is dropped and
reported, not repaired.

Resolution (brain/project_schema.yaml):
  - facts from one source lineage (source_key) are ordered by seq; the last is
    current and earlier differing values become that field's history (changes)
  - numeric fields compare the current value of each lineage; values that are
    not compatible (qualifiers respected) make the field a conflict
  - a field whose extractors all came up empty is not_found
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from brain.ingest import DB_PATH
from brain.registry import SCHEMA


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Fact:
    field: str
    value: object               # number, string or list
    display: str                # what the card shows
    source_key: str             # lineage; later seq supersedes earlier in the same lineage
    seq: int
    as_of: str                  # ISO date the statement was made
    doc_id: str
    page_no: int
    locator: str
    quote: str
    basis: str = ""
    qualifier: str = "exact"    # exact | approx | nearly | more_than
    number_text: str = ""       # the literal number that must appear in the quote
    tier: int = 0
    since: str = ""             # first as_of of the run this value belongs to
    first: "Fact | None" = None  # the first fact of that run (where the value first appeared)
    until: str = ""             # last as_of before it changed (history only)


@dataclass
class FieldRecord:
    field: str
    label: str
    status: str                 # found | conflict | not_found | not_extracted
    current: list[Fact] = field(default_factory=list)
    history: list[Fact] = field(default_factory=list)
    note: str = ""


class Pages:
    """Page text access with whitespace normalized once."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self._cache: dict[str, list[tuple[int, str, str]]] = {}
        self.tiers = dict(con.execute("SELECT id, tier FROM documents"))

    def of(self, doc_id: str) -> list[tuple[int, str, str]]:
        if doc_id not in self._cache:
            rows = self.con.execute(
                "SELECT page_no, locator, text FROM pages WHERE doc_id=? ORDER BY page_no", (doc_id,)
            ).fetchall()
            self._cache[doc_id] = [(n, loc, norm(t)) for n, loc, t in rows]
        return self._cache[doc_id]

    def find(self, doc_id: str, pattern: str, page: int | None = None, flags=re.I):
        """First match of pattern in the document: (page_no, locator, match) or None."""
        rx = re.compile(pattern, flags)
        for n, loc, text in self.of(doc_id):
            if page is not None and n != page:
                continue
            m = rx.search(text)
            if m:
                return n, loc, m
        return None


# ---------- extractors ----------

def _num(s: str) -> float:
    return float(s.replace(",", ""))


def regex_facts(pages: Pages) -> tuple[list[Fact], list[str]]:
    """Facts stated in prose, one anchored pattern each."""
    specs = [
        # capacity_mw: TCEQ lineage
        dict(field="capacity_mw", doc="tceq-application-2025-10", source="tceq-application", seq=1,
             as_of="2025-10-17",
             rx=r"nominal power generation capacity will be approximately (2,595) megawatts \(MW\)",
             value=lambda m: _num(m[1]), display=lambda m: f"{m[1]} MW",
             basis="Nominal output of 17 turbines, application as filed"),
        dict(field="capacity_mw", doc="tceq-application-2026-tc", source="tceq-application", seq=2,
             as_of="2025-12-22",
             rx=r"total nominal net output of (2,476) megawatts \(MW\) at site conditions",
             value=lambda m: _num(m[1]), display=lambda m: f"{m[1]} MW",
             basis="Same 17 turbines restated at site conditions, in the response to TCEQ's 12/9/2025 deficiency notice"),
        dict(field="capacity_mw", doc="tceq-application-2026-tc", source="tceq-application", seq=3,
             as_of="2025-12-22",
             rx=r"nominal power generation capacity will be approximately (2,869) megawatts \(MW\) at site conditions",
             value=lambda m: _num(m[1]), display=lambda m: f"{m[1]} MW",
             basis="46 turbines at site conditions, after the 12/22/2025 update added 29 Solar Titan 130 units"),
        dict(field="capacity_mw", doc="chevron-release-2026-06-22", source="chevron", seq=1,
             as_of="2026-06-22", rx=r"approximately (2\.67) gigawatts of capacity",
             value=lambda m: _num(m[1]) * 1000, display=lambda m: f"about {m[1]} GW", qualifier="approx",
             basis="Company announcement of the Microsoft agreement"),
        dict(field="capacity_mw", doc="semafor-2026-10-01", source="semafor", seq=1,
             as_of="2026-10-01", rx=r"nearly (3) gigawatts",
             value=lambda m: _num(m[1]) * 1000, display=lambda m: f"nearly {m[1]} GW", qualifier="nearly",
             basis="Press report"),

        # co2e_tpy
        dict(field="co2e_tpy", doc="tceq-pls", source="tceq-application", seq=1, as_of="2025-10-17",
             rx=r"CO2 Equivalent (11,564,704\.59)",
             value=lambda m: _num(m[1]), display=lambda m: f"{m[1]} tons/yr",
             basis="Plain language summary of the application as filed (17 turbines)"),
        dict(field="co2e_tpy", doc="tceq-application-2026-tc", source="tceq-application", seq=2,
             as_of="2026-04-10", rx=r"GHG as CO2e (13,890,275\.01)",
             value=lambda m: _num(m[1]), display=lambda m: f"{m[1]} tons/yr",
             basis="TCEQ RBLC summary of the draft permit for the updated 46 turbine project"),
        dict(field="co2e_tpy", doc="techcrunch-2026-06-22", source="eip-via-techcrunch", seq=1,
             as_of="2026-06-22", rx=r"more than (13) million tons of carbon dioxide",
             value=lambda m: _num(m[1]) * 1e6, display=lambda m: f"more than {m[1]} million tons",
             qualifier="more_than",
             basis="Environmental Integrity Project estimate as reported by TechCrunch; the quote does not state a period"),

        # who
        dict(field="developer", doc="tceq-napd", source="tceq", seq=1, as_of="2026-04-15",
             rx=r"(Energy Forge One LLC), 1500 Louisiana Street, Houston, Texas 77002- ?7308, has applied",
             value=lambda m: m[1], display=lambda m: m[1], basis="Permit applicant of record"),
        dict(field="sponsors", doc="chevron-release-2026-06-22", source="chevron", seq=1, as_of="2026-06-22",
             rx=r"Energy Forge One LLC, a wholly owned subsidiary",
             value=lambda m: "Chevron (Energy Forge One is a wholly owned subsidiary)",
             display=lambda m: "Chevron, through wholly owned Energy Forge One LLC"),
        dict(field="sponsors", doc="chevron-release-2026-06-22", source="chevron-2", seq=1, as_of="2026-06-22",
             rx=r"Chevron and Engine No\. 1 have been collaborating on the development",
             value=lambda m: "Chevron and Engine No. 1", display=lambda m: "Chevron and Engine No. 1, collaborating"),
        dict(field="sponsors", doc="semafor-2026-10-01", source="semafor", seq=1, as_of="2026-10-01",
             rx=r"a joint venture between Chevron and the newly formed energy project developer Joulent",
             value=lambda m: "Chevron and Joulent", display=lambda m: "Chevron and Joulent, joint venture"),
        dict(field="offtaker", doc="chevron-release-2026-06-22", source="chevron", seq=1, as_of="2026-06-22",
             rx=r"dedicated electricity to a (Microsoft)-operated data center",
             value=lambda m: m[1], display=lambda m: "Microsoft (co-located data center)"),
        dict(field="ppa_term_years", doc="chevron-release-2026-06-22", source="chevron", seq=1,
             as_of="2026-06-22", rx=r"under a (20)-year power purchase agreement",
             value=lambda m: _num(m[1]), display=lambda m: f"{m[1]} years"),
        dict(field="turbine_suppliers", doc="chevron-release-2026-06-22", source="chevron", seq=1,
             as_of="2026-06-22",
             rx=r"large (GE Vernova) \(NYSE: GEV\) turbines.{0,80}?additional capacity provided by (Solar Turbines), a wholly owned subsidiary of Caterpillar",
             value=lambda m: [m[1], m[2]], display=lambda m: f"{m[1]} (majority), {m[2]} (Caterpillar)"),

        # where
        dict(field="location", doc="tceq-napd", source="tceq", seq=1, as_of="2026-04-15",
             rx=r"reach the site in (Reeves County, Pecos, Texas 79780)",
             value=lambda m: m[1], display=lambda m: m[1]),

        # when
        dict(field="first_power", doc="chevron-release-2026-06-22", source="chevron", seq=1,
             as_of="2026-06-22", rx=r"First power delivery is anticipated in (2028)",
             value=lambda m: m[1], display=lambda m: m[1], basis="Company target"),
        dict(field="fid_status", doc="chevron-release-2026-06-22", source="chevron", seq=1,
             as_of="2026-06-22",
             rx=r"Final Investment Decision \(FID\), which is expected by the end of 2026",
             value=lambda m: "expected by end of 2026", display=lambda m: "Expected by the end of 2026"),
        dict(field="fid_status", doc="semafor-2026-10-01", source="semafor", seq=1, as_of="2026-10-01",
             rx=r"Texas state politics could delay the final investment decision",
             value=lambda m: "could be delayed", display=lambda m: "Could be delayed by Texas state politics"),

        # permits
        dict(field="permit_milestones", doc="tceq-nori", source="m-filed", seq=1, as_of="2025-10-17",
             rx=r"This application was submitted to the TCEQ on (October 17, 2025)",
             value=lambda m: "Application filed", display=lambda m: f"{m[1]}: application filed"),
        dict(field="permit_milestones", doc="tceq-application-2026-tc", source="m-nod", seq=1,
             as_of="2025-12-09",
             rx=r"respond to your Notice of Deficiency \(NOD\) letter dated (December 9, 2025)",
             value=lambda m: "TCEQ notice of deficiency", display=lambda m: f"{m[1]}: TCEQ notice of deficiency"),
        dict(field="permit_milestones", doc="tceq-application-2026-tc", source="m-update", seq=1,
             as_of="2025-12-22",
             rx=r"added to the project scope during an update on (December 22, 2025)",
             value=lambda m: "Application updated (29 turbines added)",
             display=lambda m: f"{m[1]}: application updated, 29 Solar Titan 130 turbines added"),
        dict(field="permit_milestones", doc="tceq-application-2026-tc", source="m-tc", seq=1,
             as_of="2026-04-15", rx=r"Technically Complete Date (4/15/2026)",
             value=lambda m: "Technically complete", display=lambda m: f"{m[1]}: technically complete (TCEQ estimate on the RBLC form)"),
        dict(field="permit_milestones", doc="tceq-meeting-notice", source="m-meeting", seq=1,
             as_of="2026-06-10", rx=r"The Public Meeting is to be held: (Wednesday, June 10, 2026) at 7:00 PM",
             value=lambda m: "Public meeting", display=lambda m: f"{m[1]}: public meeting in Pecos"),
        dict(field="permit_milestones", doc="tceq-application-2026-tc", source="m-final", seq=1,
             as_of="2026-10-15", rx=r"Estimated Final Issuance Date (10/15/2026)",
             value=lambda m: "Estimated final issuance", display=lambda m: f"{m[1]}: estimated final issuance (TCEQ estimate)"),

        # environmental
        dict(field="nox_controls", doc="tceq-pls", source="tceq", seq=1, as_of="2025-10-17",
             rx=r"controlled by (selective catalytic reduction for NOx, and oxidation catalyst for CO and VOC)",
             value=lambda m: m[1], display=lambda m: m[1][0].upper() + m[1][1:]),
        dict(field="water_source", doc="chevron-release-2026-06-22", source="chevron", seq=1,
             as_of="2026-06-22", rx=r"use (non-potable, brackish groundwater) sources for power plant operations",
             value=lambda m: m[1], display=lambda m: m[1][0].upper() + m[1][1:]),
    ]
    facts, misses = [], []
    for s in specs:
        hit = pages.find(s["doc"], s["rx"])
        if not hit:
            misses.append(f"{s['field']}: no match in {s['doc']}")
            continue
        n, loc, m = hit
        numeric = isinstance(s["value"](m), float)
        facts.append(Fact(
            field=s["field"], value=s["value"](m), display=s["display"](m), source_key=s["source"],
            seq=s["seq"], as_of=s["as_of"], doc_id=s["doc"], page_no=n, locator=loc, quote=m[0],
            basis=s.get("basis", ""), qualifier=s.get("qualifier", "exact"),
            number_text=m[1] if numeric else "", tier=pages.tiers.get(s["doc"], 0),
        ))
    return facts, misses


FLEET_RX = re.compile(
    r"(\d+) (GE 7HA\.02|Solar Titan \d+ \(T\d+\))\s+(combined|simple)-cycle turbines?"
    r"(?:\s*\([^)]*\))?(?:,\s*each with a net\s+output of\s+([\d.]+) MW)?",
    re.I,
)


def fleet_facts(pages: Pages) -> tuple[list[Fact], list[str]]:
    """Turbine lists: the 17 as filed and the 46 after the update."""
    out, misses = [], []
    for doc, seq, as_of, anchor in [
        ("tceq-application-2025-10", 1, "2025-10-17", r"operate 17 combustion turbines for onsite power generation:(.{0,400})"),
        ("tceq-application-2026-tc", 2, "2025-12-22", r"The 46 turbines and expected output are as follows:(.{0,900}?13\.55 MW)"),
    ]:
        hit = pages.find(doc, anchor)
        if not hit:
            misses.append(f"turbine_fleet: no match in {doc}")
            continue
        n, loc, m = hit
        items = [
            {"count": int(c), "model": model, "cycle": cyc.lower(), "unit_mw": float(mw) if mw else None}
            for c, model, cyc, mw in FLEET_RX.findall(m[0])
        ]
        total = sum(i["count"] for i in items)
        display = "; ".join(
            f"{i['count']} x {i['model']} {i['cycle']} cycle" + (f" ({i['unit_mw']:g} MW each)" if i["unit_mw"] else "")
            for i in items
        )
        out.append(Fact("turbine_fleet", items, f"{total} turbines: {display}", "tceq-application", seq, as_of,
                        doc, n, loc, m[0], basis="Application as filed" if seq == 1 else "After the 12/22/2025 update",
                        tier=pages.tiers[doc]))
    return out, misses


def _fields(text: str) -> list[dict]:
    """ERCOT page text -> list of row dicts ('column: value' blocks)."""
    rows = []
    for block in text.split("\n\n")[1:]:
        row = {}
        for line in block.splitlines():
            k, _, v = line.partition(": ")
            row[k.strip()] = v.strip()
        rows.append(row)
    return rows


def ercot_facts(pages: Pages) -> tuple[list[Fact], list[str]]:
    """Queue entries, battery size and interconnection point, month by month."""
    con = pages.con
    docs = con.execute(
        "SELECT id, published FROM documents WHERE kind='queue_report' ORDER BY published"
    ).fetchall()
    facts: list[Fact] = []
    for seq, (doc_id, published) in enumerate(docs, start=1):
        for page_no, locator, raw in con.execute(
            "SELECT page_no, locator, text FROM pages WHERE doc_id=? ORDER BY page_no", (doc_id,)
        ):
            cancelled = "Cancellation" in locator
            for row in _fields(raw):
                name = row.get("Project Name", "")
                if not name.lower().startswith("kilby1"):
                    continue  # skips the unrelated Haskell County "Kilby Station" rows
                inr = row.get("INR", "")
                quote = f"INR: {inr} ... Project Name: {name}"
                entry = {
                    "inr": inr, "name": name, "mw": row.get("Capacity (MW)") or row.get("MW **"),
                    "fuel": row.get("Fuel"), "technology": row.get("Technology"),
                    "projected_cod": row.get("Projected COD"), "study_phase": row.get("GIM Study Phase"),
                    "status": "cancelled" if cancelled else "active",
                    "cancel_date": row.get("Cancel Date"),
                }
                if cancelled:
                    quote += f" ... Cancel Date: {entry['cancel_date']}"
                disp = (f"{inr} {name}: cancelled {entry['cancel_date']} ({entry['mw']} MW {entry['fuel']})" if cancelled else
                        f"{inr} {name}: {entry['mw']} MW {entry['fuel']}/{entry['technology']}, "
                        f"COD {entry['projected_cod']}, {entry['study_phase']}")
                facts.append(Fact("queue_entries", entry, disp, f"ercot-{inr}", seq, published, doc_id,
                                  page_no, locator, quote, basis="ERCOT GIS report", tier=1))
                if "BESS" in name and entry["mw"]:
                    q = f"Project Name: {name} ... Capacity (MW): {entry['mw']}"
                    facts.append(Fact("battery_storage_mw", float(entry["mw"]), f"{entry['mw']} MW", "ercot",
                                      seq, published, doc_id, page_no, locator, q,
                                      basis=f"ERCOT queue, {inr}", number_text=entry["mw"], tier=1))
                if row.get("POI Location") and not cancelled:
                    facts.append(Fact("interconnection_point", row["POI Location"], row["POI Location"],
                                      f"ercot-{inr}", seq, published, doc_id, page_no, locator,
                                      f"Project Name: {name} ... POI Location: {row['POI Location']}",
                                      basis=f"ERCOT queue, {inr}", tier=1))
    dedup = facts
    # earliest projected COD in the latest report, as a second first_power basis
    latest = [f for f in dedup if f.field == "queue_entries" and f.seq == len(docs) and f.value["status"] == "active"]
    if latest:
        first = min(latest, key=lambda f: f.value["projected_cod"])
        dedup.append(Fact("first_power", first.value["projected_cod"],
                          f"{first.value['projected_cod']} ({first.value['name']}, {first.value['mw']} MW)",
                          "ercot", 1, first.as_of, first.doc_id, first.page_no, first.locator,
                          f"Project Name: {first.value['name']} ... Projected COD: {first.value['projected_cod']}",
                          basis="Earliest projected COD in the ERCOT queue", tier=1))
    return dedup, ([] if docs else ["queue_entries: no ERCOT reports ingested"])


def permit_status_facts(pages: Pages) -> tuple[list[Fact], list[str]]:
    con = pages.con
    docs = con.execute(
        "SELECT id, published FROM documents WHERE kind='status_snapshot' ORDER BY published"
    ).fetchall()
    out = []
    for seq, (doc_id, published) in enumerate(docs, start=1):
        hit = pages.find(doc_id, r"(Administrative Complete Application|Technically Complete Application|Permit Issued|Withdrawn|Denied)")
        if hit:
            n, loc, m = hit
            out.append(Fact("air_permit_status", m[1], m[1], "tceq-pending", seq, published, doc_id, n, loc,
                            m[0], basis="TCEQ pending permit list", tier=1))
    return out, ([] if out else ["air_permit_status: no snapshot matched"])


# ---------- verification ----------

def verify(fact: Fact, pages: Pages) -> str | None:
    """None if the fact is grounded, else the reason it is not."""
    text = next((t for n, _, t in pages.of(fact.doc_id) if n == fact.page_no), None)
    if text is None:
        return "cited page does not exist"
    parts = [p for p in norm(fact.quote).split(" ... ") if p]
    if not all(p in text for p in parts):
        return "quote not found on the cited page"
    if fact.number_text and fact.number_text not in fact.quote:
        return f"number {fact.number_text} not in quote"
    return None


# ---------- resolution ----------

def compatible(a: Fact, b: Fact) -> bool:
    """Can two numeric statements both be true?"""
    def rng(f: Fact) -> tuple[float, float]:
        v = float(f.value)
        return {
            "exact": (v, v),
            "approx": (v * 0.98, v * 1.02),
            "nearly": (v * 0.9, v),
            "more_than": (v, float("inf")),
        }[f.qualifier]
    (a0, a1), (b0, b1) = rng(a), rng(b)
    return a0 <= b1 and b0 <= a1


def resolve(facts: list[Fact], schema: dict, attempted: set[str]) -> list[FieldRecord]:
    out = []
    for name, spec in schema["fields"].items():
        mine = [f for f in facts if f.field == name]
        rec = FieldRecord(name, spec["label"], "not_extracted")
        if not mine:
            rec.status = "not_found" if name in attempted else "not_extracted"
            out.append(rec)
            continue
        lineages: dict[str, list[Fact]] = {}
        for f in mine:
            lineages.setdefault(f.source_key, []).append(f)
        for key, fs in lineages.items():
            fs.sort(key=lambda f: (f.seq, f.as_of))
            runs: list[list[Fact]] = []
            for f in fs:
                if runs and runs[-1][-1].display == f.display:
                    runs[-1].append(f)
                else:
                    runs.append([f])
            for run in runs[:-1]:
                h = run[-1]
                h.since, h.until, h.first = run[0].as_of, run[-1].as_of, run[0]
                rec.history.append(h)
            cur = runs[-1][-1]
            cur.since, cur.first = runs[-1][0].as_of, runs[-1][0]
            rec.current.append(cur)
        if spec.get("type") != "number":
            # identical statements from parallel lineages (e.g. four queue parts at one substation)
            seen: dict[str, Fact] = {}
            for f in sorted(rec.current, key=lambda f: (f.tier, f.as_of)):
                seen.setdefault(f.display, f)
            rec.current = list(seen.values())
        rec.history.sort(key=lambda f: (f.since, f.source_key))
        rec.current.sort(key=lambda f: (f.tier, f.as_of))
        rec.status = "found"
        if spec.get("type") == "number" and len(rec.current) > 1:
            clashes = [(a, b) for i, a in enumerate(rec.current) for b in rec.current[i + 1:]
                       if not compatible(a, b)]
            if clashes:
                rec.status = "conflict"
                rec.note = "; ".join(f"{a.display} vs {b.display}" for a, b in clashes)
        out.append(rec)
    return out


# ---------- build ----------

FACTS_SQL = """
DROP TABLE IF EXISTS facts;
CREATE TABLE facts (
  field TEXT, value TEXT, display TEXT, source_key TEXT, seq INTEGER, as_of TEXT,
  doc_id TEXT, page_no INTEGER, locator TEXT, quote TEXT, basis TEXT, qualifier TEXT,
  tier INTEGER, role TEXT, since TEXT, until TEXT,  -- role: current | history
  first_doc_id TEXT, first_page_no INTEGER, first_locator TEXT, first_quote TEXT
);
DROP TABLE IF EXISTS record;
CREATE TABLE record (field TEXT PRIMARY KEY, label TEXT, status TEXT, note TEXT, position INTEGER);
"""


def build(db_path: Path = DB_PATH) -> tuple[list[FieldRecord], list[str]]:
    con = sqlite3.connect(db_path)
    pages = Pages(con)
    schema = yaml.safe_load(SCHEMA.read_text())
    facts, problems = [], []
    attempted: set[str] = set()
    for extractor in (regex_facts, fleet_facts, ercot_facts, permit_status_facts):
        fs, misses = extractor(pages)
        problems += misses
        attempted |= {m.split(":")[0] for m in misses} | {f.field for f in fs}
        facts += fs
    grounded = []
    for f in facts:
        why = verify(f, pages)
        if why:
            problems.append(f"dropped {f.field} from {f.doc_id} {f.locator}: {why}")
        else:
            grounded.append(f)
    records = resolve(grounded, schema, attempted)

    con.executescript(FACTS_SQL)
    for pos, r in enumerate(records):
        con.execute("INSERT INTO record VALUES (?,?,?,?,?)", (r.field, r.label, r.status, r.note, pos))
        for role, fs in (("current", r.current), ("history", r.history)):
            for f in fs:
                con.execute(
                    "INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f.field, json.dumps(f.value), f.display, f.source_key, f.seq, f.as_of, f.doc_id,
                     f.page_no, f.locator, f.quote, f.basis, f.qualifier, f.tier, role, f.since, f.until,
                     *((f.first.doc_id, f.first.page_no, f.first.locator, f.first.quote) if f.first
                       else (f.doc_id, f.page_no, f.locator, f.quote))),
                )
    con.commit()
    con.close()
    return records, problems


if __name__ == "__main__":
    records, problems = build()
    for r in records:
        print(f"[{r.status:>13}] {r.label}")
        for f in r.current:
            print(f"                {f.display}   <- {f.doc_id} {f.locator}")
        for f in r.history:
            span = f.since if f.since == f.until else f"{f.since} to {f.until}"
            print(f"        before: {f.display}   ({span}, {f.doc_id} {f.locator})")
        if r.note:
            print(f"        note: {r.note}")
    for p in problems:
        print("PROBLEM:", p)
