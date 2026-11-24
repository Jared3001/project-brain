"""Consistency check: what each source says about the same thing.

A claim is one source's statement about one topic (capacity, first power, grid
connection, ownership, emissions, water, economic impact). Claims are read with
patterns anchored on each document's own wording, or taken from facts the record
already verified. Each claim keeps its page and a quote that is checked against
that page; a pattern that stops matching drops its claim and is reported, never
filled in.

Topics are then compared with plain rules, not a model:
  - numbers are normalized (GW to MW) and called a disagreement when they spread
    more than 3 percent
  - years are compared as years; a month counts when one source gives a date
  - categories (grid connection, ownership) are grouped by meaning, and grouped
    by date so a change over time reads as a change, not a contradiction
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache

PARTY = {
    "permit_application": "Regulatory filing", "permit_notice": "Regulatory filing",
    "status_snapshot": "Regulatory filing", "queue_report": "ERCOT queue",
    "company_release": "Company", "press": "Press", "analysis": "Analysis",
}


@dataclass
class Claim:
    topic: str
    doc_id: str
    page_no: int
    quote: str
    value: object            # MW as float, year as int, or a category key
    display: str
    date: str
    source: str              # publisher, e.g. "Chevron", "Semafor"
    party: str               # Regulatory filing, Company, Press, Analysis, ERCOT queue
    tier: int
    note: str = ""
    computed: bool = False
    extra: dict = field(default_factory=dict)


# (topic, doc_id, pattern, value, display, note). The pattern is whitespace tolerant.
SPECS = [
    # capacity, MW
    ("capacity", "chevron-release-2026-06-22", r"expected to deliver approximately 2.67 gigawatts of capacity", 2670, "about 2.67 GW", ""),
    ("capacity", "joulent-release-national-grid", r"Kilby will deliver approximately 2.67 gigawatts of generation capacity", 2670, "about 2.67 GW", ""),
    ("capacity", "eenews-2026-06", r"plans to build 2.67 gigawatts of natural gas-fueled generation over time", 2670, "2.67 GW", "attributed to Chevron"),
    ("capacity", "energycapital-joulent-launch", r"deliver about 2.67 gigawatts of power", 2670, "about 2.67 GW", ""),
    ("capacity", "powereng-national-grid-joulent", r"Kilby is a 2.67 GW co-located power facility", 2670, "2.67 GW", ""),
    ("capacity", "latitude-national-grid-joulent", r"Kilby aims to deliver 2.67 GW of power", 2670, "2.67 GW", ""),
    ("capacity", "semafor-2026-10-01", r"set to use nearly 3 gigawatts", 3000, "nearly 3 GW", "upper bound, not a figure"),
    ("capacity", "frontier-grid-2026-07-28", r"Permit envelope Approximately 2.869 GW across 46 turbines", 2869, "about 2.869 GW (permit envelope)", "also reports 2.67 GW as commercial scale"),
    ("capacity", "compute-atlas", r"PPA with Microsoft for ~2.67 GW", 2670, "about 2.67 GW", ""),
    # first power, year
    ("first_power", "chevron-release-2026-06-22", r"First power delivery is anticipated in 2028", 2028, "2028", ""),
    ("first_power", "powereng-national-grid-joulent", r"targeting first power delivery by 2028", 2028, "by 2028", ""),
    ("first_power", "latitude-national-grid-joulent", r"We are planning to achieve first power in 2028", 2028, "2028", "quoting Joulent's Brian Boland"),
    ("first_power", "semafor-2026-10-01", r"on track to produce its first electrons by 2028", 2028, "by 2028", ""),
    ("first_power", "frontier-grid-2026-07-28", r"first power is targeted by 2028", 2028, "by 2028", ""),
    ("first_power", "compute-atlas", r"first power is anticipated in 2028", 2028, "2028", ""),
    # grid connection, category
    ("grid", "eenews-2026-06", r"would not initially be connected to the grid", "not_connected_initially", "Not connected to the grid at first", "attributed to Chevron"),
    ("grid", "semafor-2026-10-01", r"“behind-the-meter” gas turbines — that is, unconnected to the state grid", "behind_meter", "Behind the meter, unconnected to the state grid", ""),
    ("grid", "latitude-national-grid-joulent", r"entrance into the growing behind-the-meter market", "behind_meter", "Behind the meter", ""),
    ("grid", "chevron-release-2026-06-22", r"deliver reliable, dispatchable electricity directly to Microsoft while aiming to mitigate impacts on the regional grid", "direct_to_customer", "Power delivered directly to Microsoft", ""),
    ("grid", "joulent-power-stack", r"opportunity to dispatch into and/or draw from the grid", "grid_two_way", "Able to send power to and draw from the grid", "describes Joulent's platform in general, not Kilby by name"),
    # ownership, category
    ("ownership", "chevron-release-2026-06-22", r"Energy Forge One LLC, a wholly owned subsidiary, has signed", "chevron_wholly_owned", "Energy Forge One is a wholly owned Chevron subsidiary", ""),
    ("ownership", "joulent-release-national-grid", r"Joulent has also formalized a 50% ownership interest in its first project, Kilby", "jv_50_50", "Joulent holds 50% of Kilby", ""),
    ("ownership", "latitude-national-grid-joulent", r"developing through a 50-50 joint venture with Chevron", "jv_50_50", "50/50 joint venture, Joulent and Chevron", ""),
    ("ownership", "powereng-national-grid-joulent", r"in a 50/50 partnership with Chevron Corporation", "jv_50_50", "50/50 partnership with Chevron", ""),
    ("ownership", "semafor-2026-10-01", r"a joint venture between Chevron and the newly formed energy project developer Joulent", "jv_50_50", "Joint venture, Chevron and Joulent", "no split given"),
    ("ownership", "frontier-grid-2026-07-28", r"developed through a 50/50 Joulent-Chevron partnership", "jv_50_50", "50/50 Joulent and Chevron", ""),
    # emissions, tons CO2 per year
    ("co2", "techcrunch-2026-06-22", r"potentially release more than 13 million tons of carbon dioxide", 13_000_000, "more than 13 million tons", "a floor, not a figure"),
    # water
    ("water", "chevron-release-2026-06-22", r"Kilby plans to use non-potable, brackish groundwater sources", "brackish", "Non-potable, brackish groundwater", ""),
    ("water", "frontier-grid-2026-07-28", r"nonpotable / brackish sources are proposed. Initial fill, construction water and rights still require", "brackish", "Brackish sources proposed; water rights still to confirm", ""),
    # economic impact
    ("economy", "chevron-release-2026-06-22", r"more than \$10 billion in state and local tax revenue, support almost 2,000 jobs", "chevron_econ", "More than $10B in state and local tax revenue, almost 2,000 jobs", ""),
]

TOPICS = {
    "capacity": ("Generating capacity", "number"),
    "first_power": ("First power", "year"),
    "grid": ("Grid connection", "category"),
    "ownership": ("Who owns Kilby", "category"),
    "co2": ("CO2 emissions", "number"),
    "water": ("Water source", "category"),
    "economy": ("Tax revenue and jobs", "category"),
}
GRID_LABELS = {
    "not_connected_initially": "Off grid at first", "behind_meter": "Behind the meter", "direct_to_customer": "Direct to customer",
    "grid_two_way": "Two way grid connection", "ercot_queue": "In the ERCOT generation queue",
}
GRID_ISLAND = {"not_connected_initially", "behind_meter", "direct_to_customer"}


def _connect():
    from brain.ingest import DB_PATH
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _rx(pattern: str) -> re.Pattern:
    # literal text with tolerant whitespace; "\$" stays escaped, "." means a literal dot
    parts = pattern.split()
    return re.compile(r"\s+".join(re.escape(p.replace("\\$", "$")) for p in parts))


@lru_cache(maxsize=1)
def claims() -> tuple[tuple[Claim, ...], tuple[str, ...]]:
    """All claims, plus a list of specs that failed to match (should be empty)."""
    con = _connect()
    con.row_factory = sqlite3.Row
    try:
        docs = {r["id"]: r for r in con.execute("SELECT * FROM documents")}
        out, misses = [], []
        for topic, doc_id, pat, value, display, note in SPECS:
            doc = docs.get(doc_id)
            if doc is None:
                misses.append(f"{topic}: {doc_id} not in corpus")
                continue
            rx = _rx(pat)
            hit = None
            for r in con.execute("SELECT page_no, text FROM pages WHERE doc_id=? ORDER BY page_no", (doc_id,)):
                m = rx.search(r["text"])
                if m:
                    hit = (r["page_no"], " ".join(m.group(0).split()))
                    break
            if hit is None:
                misses.append(f"{topic}: no match in {doc_id}")
                continue
            out.append(Claim(topic, doc_id, hit[0], hit[1], value, display, doc["published"],
                             doc["publisher"].split(" via ")[0], PARTY.get(doc["kind"], doc["kind"]), doc["tier"], note))
        out += _from_record(con, docs)
    finally:
        con.close()
    return tuple(out), tuple(misses)


def _fact_rows(con, field_name: str, role: str = "current"):
    return [dict(r) for r in con.execute("SELECT * FROM facts WHERE field=? AND role=? ORDER BY as_of", (field_name, role))]


def _from_record(con, docs) -> list[Claim]:
    """Claims the record already extracted and verified: the permit filing and the ERCOT queue."""
    out = []
    for f in _fact_rows(con, "capacity_mw"):
        d = docs[f["doc_id"]]
        if d["kind"] == "permit_application":
            out.append(Claim("capacity", f["doc_id"], f["page_no"], f["quote"], float(json.loads(f["value"])), f["display"],
                             f["as_of"], "TCEQ permit file", "Regulatory filing", d["tier"], "46 turbines at site conditions"))
    for f in _fact_rows(con, "co2e_tpy"):
        d = docs[f["doc_id"]]
        if d["tier"] == 1:
            out.append(Claim("co2", f["doc_id"], f["page_no"], f["quote"], float(json.loads(f["value"])), f["display"],
                             f["as_of"], "TCEQ permit file", "Regulatory filing", 1, "CO2e, potential to emit"))
    queue = [f for f in _fact_rows(con, "queue_entries") if json.loads(f["value"]).get("status") != "cancelled"]
    if queue:
        vals = [json.loads(f["value"]) for f in queue]
        gas = sum(float(v["mw"]) for v in vals if v.get("fuel") == "GAS")
        bess = sum(float(v["mw"]) for v in vals if v.get("fuel") != "GAS")
        newest = max(queue, key=lambda f: f["as_of"])
        d = docs[newest["doc_id"]]
        out.append(Claim("capacity", newest["doc_id"], newest["page_no"], newest["quote"], gas, f"{gas:,.0f} MW gas, plus {bess:,.0f} MW battery",
                         newest["as_of"], "ERCOT queue", "ERCOT queue", d["tier"], f"sum of {len(vals)} active queue entries", computed=True))
        dated = sorted((v["projected_cod"], f) for v, f in zip(vals, queue) if v.get("projected_cod"))
        if dated:
            cod, f = dated[0]
            v = json.loads(f["value"])
            out.append(Claim("first_power", f["doc_id"], f["page_no"], f["quote"], int(cod[:4]), f"{_d(cod)}, projected COD",
                             f["as_of"], "ERCOT queue", "ERCOT queue", docs[f["doc_id"]]["tier"],
                             f"{v['inr']} {v['name']}, {v['mw']} MW, the earliest active entry", extra={"date": cod}))
        point = _fact_rows(con, "interconnection_point")
        if point:
            p = point[-1]
            out.append(Claim("grid", p["doc_id"], p["page_no"], p["quote"], "ercot_queue",
                             f"Generation interconnection requests at {p['display']}", p["as_of"], "ERCOT queue", "ERCOT queue",
                             docs[p["doc_id"]]["tier"], f"{len(vals)} active Kilby1 entries"))
    return out


# ---- comparison -----------------------------------------------------------------

@dataclass
class Finding:
    topic: str
    label: str
    verdict: str            # disagree | changed | tension | consistent | single
    headline: str
    detail: list[str]
    claims: list[Claim]

    @property
    def rank(self) -> int:
        return ["disagree", "tension", "changed", "single", "consistent"].index(self.verdict)


def _d(iso: str) -> str:
    import datetime as dt
    return dt.date.fromisoformat(iso[:10]).strftime("%b %-d, %Y")


def _mw(v: float) -> str:
    return f"{v / 1000:.2f} GW" if v >= 1000 else f"{v:,.0f} MW"


def compare(topic: str, cs: list[Claim]) -> Finding:
    label, kind = TOPICS[topic]
    cs = sorted(cs, key=lambda c: (c.date, c.tier))
    sources = {c.source for c in cs}
    if len(sources) <= 1:
        return Finding(topic, label, "single", f"Only {cs[0].source} says this", [
            "No other source in the corpus states it, so it can't be checked against anything."], cs)

    if kind == "number":
        exact = [c for c in cs if not c.note.startswith(("upper bound", "a floor"))]
        vals = sorted({c.value for c in exact})
        spread = (vals[-1] - vals[0]) / vals[0] if vals and vals[0] else 0
        if topic == "co2":
            floor = next((c for c in cs if c.note.startswith("a floor")), None)
            reg = max((c for c in cs if c.party == "Regulatory filing"), key=lambda c: c.date, default=None)
            if floor and reg and reg.value >= floor.value:
                return Finding(topic, label, "consistent", "Press and the permit filing agree", [
                    f"{floor.source} says {floor.display}; the latest permit filing says {reg.display}, which is consistent with that floor."], cs)
        if spread <= 0.03:
            amount = f"{vals[0]:,.0f} tons a year" if topic == "co2" else _mw(vals[0])
            return Finding(topic, label, "consistent", f"All sources say about {amount}", [], cs)
        groups: dict[float, list[Claim]] = {}
        for c in cs:
            groups.setdefault(c.value, []).append(c)
        common = max(groups.items(), key=lambda kv: len(kv[1]))
        first = min(common[1], key=lambda c: c.date)
        detail = [f"{len(common[1])} sources say {_mw(common[0])}; the first was {first.source} on {_d(first.date)}."]
        for v, g in sorted(groups.items()):
            if v != common[0]:
                names = ", ".join(sorted({c.source for c in g}))
                note = next((c.note for c in g if c.note), "")
                detail.append(f"{names}: {g[0].display}" + (f" ({note})" if note else "") + ".")
        detail.append("No source in the corpus explains the gap between the headline figure and the regulatory and queue numbers.")
        return Finding(topic, label, "disagree", f"Sources give {len(groups)} different figures", detail, cs)

    if kind == "year":
        years = {c.value for c in cs}
        dated = [c for c in cs if c.extra.get("date")]
        if len(years) == 1 and not dated:
            return Finding(topic, label, "consistent", f"Every source says {cs[0].value}", [], cs)
        public = [c for c in cs if not c.extra.get("date")]
        detail = [f"{len(public)} public statements say {', '.join(sorted({c.display for c in public}))}."]
        for c in dated:
            detail.append(f"The {c.source} lists an earlier date: {c.display} ({c.note}).")
        detail.append("A queue COD is the developer's own projection to ERCOT, so the two are not strictly the same milestone, "
                      "but the earliest block is dated before every public statement.")
        verdict = "disagree" if len(years) > 1 else "tension"
        return Finding(topic, label, verdict, "The ERCOT queue is earlier than the public date", detail, cs)

    # categories
    if topic == "grid":
        island = [c for c in cs if c.value in GRID_ISLAND]
        connected = [c for c in cs if c.value not in GRID_ISLAND]
        if island and connected:
            return Finding(topic, label, "tension", "Described as off grid, while grid requests are on file", [
                f"{', '.join(sorted({c.source for c in island}))} describe the plant as behind the meter or not connected.",
                "Two sources point the other way. "
                + "; ".join(f"{c.source}: {c.display}" + (f" ({c.note})" if c.note else "") for c in connected) + ".",
                "E&E News' wording, not connected at first, reconciles the two: island at launch, grid tie later. "
                "No source states when the grid connection is planned."], cs)
        return Finding(topic, label, "consistent", "Sources agree", [], cs)

    keys = []
    for c in cs:
        if c.value not in keys:
            keys.append(c.value)
    if len(keys) == 1:
        return Finding(topic, label, "consistent", "Sources agree", [], cs)
    first_of = {k: min((c for c in cs if c.value == k), key=lambda c: c.date) for k in keys}
    ordered = sorted(keys, key=lambda k: first_of[k].date)
    if topic == "ownership":
        a, b = first_of[ordered[0]], first_of[ordered[-1]]
        return Finding(topic, label, "changed", "Wholly owned in June, a 50/50 joint venture from July", [
            f"{a.source}, {_d(a.date)}: {a.display}.",
            f"{b.source}, {_d(b.date)}: {b.display}. Every later source repeats the 50/50 structure.",
            "Both can be true in sequence: Joulent says it formalized its stake with the National Grid deal. "
            "The permit applicant is still Energy Forge One LLC."], cs)
    return Finding(topic, label, "disagree", "Sources disagree", [f"{c.source}: {c.display}" for c in cs], cs)


@lru_cache(maxsize=1)
def findings() -> tuple[Finding, ...]:
    cs, _ = claims()
    by: dict[str, list[Claim]] = {}
    for c in cs:
        by.setdefault(c.topic, []).append(c)
    out = [compare(t, by[t]) for t in TOPICS if t in by]
    return tuple(sorted(out, key=lambda f: f.rank))


def grid_label(key: str) -> str:
    return GRID_LABELS.get(key, key)
