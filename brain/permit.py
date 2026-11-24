"""The draft air permit's special conditions, version by version.

The TCEQ permit file carries several copies of the draft special conditions for
permit 181895, each one following the email thread it was traded in (TCEQ draft,
applicant edits, TCEQ reply, and so on). This module finds those copies, splits
each into numbered conditions, and diffs every condition across versions.

Nothing is generated. Each condition keeps the page it starts on, and every
structured value (turbine groups, ppm limits, startup minutes, fuel sulfur) is
read with a pattern and carries a quote that is checked against its page.
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache

from markupsafe import Markup, escape

DOC_ID = "tceq-application-2026-tc"
HEADER = re.compile(r"^\s*Special Conditions\s*\n\s*Permit Numbers? 181895[^\n]*\n(\s*Page \d+\s*\n)?")
SENT = re.compile(r"Sent:\s*\n?\s*(?:[A-Z][a-z]+day, )?([A-Z][a-z]+ \d{1,2}, \d{4})")
FROM = re.compile(r"From:\s*\n?\s*([^\n]*?)\s*(?:<([^>\s]+)>)?\s*\n")
MIN_RUN = 6  # pages; shorter runs are stray single page excerpts, not a full draft

TOPICS = [
    # (key, label, EPC design constraint?, patterns)
    ("equipment", "Equipment authorized", True, r"authorizes|nominal capacity|heat input of|configuration"),
    ("limits", "Emission limits", True, r"ppmvd|concentration|lb/hr|emission rate|shall not exceed the following"),
    ("mss", "Startup, shutdown, maintenance", True, r"startup|shutdown|\bMSS\b|maintenance activit"),
    ("fuel", "Fuel", True, r"fuel|sulfur|natural gas"),
    ("monitoring", "Monitoring and testing", True, r"CEMS|monitor|stack (?:test|sampling)|sampl|performance test|calibrat"),
    ("ancillary", "Auxiliary boiler, black start, emergency engines", True, r"Black Start|Auxiliary Boiler|Emergency Diesel|Fire Pump"),
    ("site", "Tanks, piping, ammonia, wastewater", True, r"storage tank|piping|valves|ammonia handling|wastewater"),
    ("records", "Records and reporting", False, r"records?\b|report|maintain(?:ed)? (?:on site|at the plant)"),
    ("federal", "Federal rules", False, r"40 CFR|Subpart"),
]


@dataclass
class Condition:
    number: int
    section: str
    text: str
    page_no: int
    topics: list[str] = field(default_factory=list)
    last_page: int = 0

    @property
    def topic(self) -> str:
        return self.topics[0] if self.topics else "other"

    @property
    def summary(self) -> str:
        """First sentence, trimmed, for lists."""
        s = re.split(r"(?<=[a-z0-9\)])\.\s|:\s*\n|\n", self.text, maxsplit=1)[0]
        return s if len(s) <= 220 else s[:217].rsplit(" ", 1)[0] + "..."


@dataclass
class Comment:
    """A Word margin comment printed at the foot of a redline page, e.g. 'Commented [HP2R1]: Accepted'."""
    tag: str          # XL1, HP2R1
    num: int
    reply_to: int | None
    author: str
    org: str
    text: str
    page_no: int



@dataclass
class Version:
    key: str
    first_page: int
    last_page: int
    thread_date: dt.date | None
    thread_from: str
    conditions: dict[int, Condition]
    comments: list[Comment] = field(default_factory=list)
    on_draft_thread: bool = False
    label: str = ""
    n: int = 0

    @property
    def redline(self) -> bool:
        return bool(self.comments)


def _connect() -> sqlite3.Connection:
    from brain.ingest import DB_PATH
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _pages(con) -> dict[int, str]:
    return {p: t for p, t in con.execute("SELECT page_no, text FROM pages WHERE doc_id=? ORDER BY page_no", (DOC_ID,))}


def _runs(pages: dict[int, str]) -> list[tuple[int, int]]:
    hits = [p for p, t in pages.items() if HEADER.match(t)]
    runs, start, prev = [], None, None
    for p in hits:
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            runs.append((start, prev))
            start = prev = p
    if start is not None:
        runs.append((start, prev))
    return [r for r in runs if r[1] - r[0] + 1 >= MIN_RUN]


def _thread_meta(pages: dict[int, str], lo: int, hi: int) -> tuple[dt.date | None, str, bool]:
    """Newest Sent date and its sender in the email pages just before a draft copy.

    Messages whose subject names the draft permit win; the span can also hold unrelated
    threads (modeling, NAPD translation) that were filed in between.
    """
    span = [p for p in range(lo, hi) if re.search(r"Subject:\s*\n?\s*[^\n]*Draft Permit", pages.get(p, ""))]
    on_thread = bool(span)
    best, sender = None, ""
    for p in (span or range(lo, hi)):
        t = pages.get(p, "")
        for m in SENT.finditer(t):
            try:
                d = dt.datetime.strptime(m.group(1), "%B %d, %Y").date()
            except ValueError:
                continue
            if best is None or d > best:
                best = d
                head = t[max(0, m.start() - 400):m.start()]
                fr = list(FROM.finditer(head + "\n"))
                sender = (fr[-1].group(2) or fr[-1].group(1)) if fr else ""
    return best, sender, on_thread


COND_LINE = re.compile(r"^\s*(\d{1,2})\.\s*$")


AUTHORS = {"XL": ("Xu Liu", "ERM, for the applicant"), "XU": ("Xu Liu", "ERM, for the applicant"),
           "HP": ("Huy Pham", "TCEQ")}
COMMENT = re.compile(r"Commented \[([A-Za-z]+)(\d+)(?:R(\d+))?\]:\s*(.*?)(?=Commented \[|\Z)", re.S)


def _body_and_comments(text: str, page_no: int) -> tuple[str, list[Comment]]:
    i = text.find("Commented [")
    if i < 0:
        return text, []
    tail = re.sub(r"(?m)^Formatted:.*$", "", text[i:])
    out = []
    for m in COMMENT.finditer(tail):
        who, org = AUTHORS.get(m.group(1).upper(), (m.group(1), ""))
        out.append(Comment(f"{m.group(1)}{m.group(2)}" + (f"R{m.group(3)}" if m.group(3) else ""), int(m.group(2)),
                           int(m.group(3)) if m.group(3) else None, who, org, " ".join(m.group(4).split()), page_no))
    return text[:i], out


def _split(pages: dict[int, str], start: int, end: int) -> tuple[dict[int, Condition], list[Comment]]:
    lines: list[tuple[int, str]] = []
    comments: list[Comment] = []
    for p in range(start, end + 1):
        body, cs = _body_and_comments(HEADER.sub("", pages[p], count=1), p)
        comments += cs
        lines += [(p, ln) for ln in body.split("\n")]
    conds: dict[int, Condition] = {}
    cur_n, cur_page, buf, section, pending = None, None, [], "", ""
    for i, (p, ln) in enumerate(lines):
        m = COND_LINE.match(ln)
        if m and int(m.group(1)) == (cur_n or 0) + 1:
            if cur_n is not None:
                conds[cur_n] = Condition(cur_n, section, _norm(buf), cur_page)
            if pending:
                section, pending = pending, ""
            cur_n, cur_page, buf = int(m.group(1)), p, []
            continue
        s = ln.strip()
        # a heading is a short title case line with no end punctuation right before the next condition number
        nxt = next((l for _, l in lines[i + 1:i + 3] if l.strip()), "")
        if s and COND_LINE.match(nxt) and _is_heading(s):
            pending = s
            continue
        if cur_n is not None:
            buf.append(ln)
    if cur_n is not None:
        conds[cur_n] = Condition(cur_n, section, _norm(buf), cur_page)
    nums = sorted(conds)
    for i, n in enumerate(nums):
        c = conds[n]
        c.topics = _topics(c.section, c.text)
        c.last_page = conds[nums[i + 1]].page_no if i + 1 < len(nums) else end
    return conds, comments


def _is_heading(s: str) -> bool:
    if len(s) > 80 or s.endswith((".", ",", ";", ":")) or s[0].isdigit() or re.search(r"\bCFR\b|Table|Subpart", s):
        return False
    words = [w for w in re.split(r"\s+", s) if w.isalpha()]
    return len(words) >= 2 and sum(w[0].isupper() for w in words) >= max(1, len(words) - 2)


SECTION_TOPIC = [
    (r"Federal", "federal"), (r"Opacity|Greenhouse", "limits"), (r"Boiler|Black Start|Emergency Engines", "ancillary"),
    (r"Storage Tank|Wastewater|Ammonia|Piping", "site"), (r"Determination of Compliance|Demonstration of Compliance", "monitoring"),
    (r"Planned Maintenance|Startup", "mss"), (r"Recordkeeping", "records"),
]


def _topics(section: str, text: str) -> list[str]:
    # ancillary and site are section topics only; their words appear in many other conditions
    hits = {k: len(re.findall(rx, text, re.I)) for k, _, _, rx in TOPICS if k not in ("ancillary", "site")}
    ranked = [k for k, n in sorted(hits.items(), key=lambda kv: -kv[1]) if n]
    lead = next((t for rx, t in SECTION_TOPIC if re.search(rx, section)), None)
    if lead is None and "ppmvd" in text:
        lead = "limits"
    if lead:
        ranked = [lead] + [k for k in ranked if k != lead]
    return ranked


def _norm(buf: list[str]) -> str:
    t = "\n".join(buf).strip()
    t = re.sub(r"[ \t]+\n", "\n", t)
    # list markers "A." / "(1)" sit on their own lines in the PDF text; rejoin them with their item
    t = re.sub(r"\n\s*([A-Z]|\(\d+\)|\([a-z]\))\.?\s*\n", lambda m: f"\n{m.group(1)}{'.' if m.group(1).isalpha() and len(m.group(1)) == 1 else ''} ", t)
    t = re.sub(r"(?<!\n)\n(?!\n|[A-Z]\. |\(\d+\) |\([a-z]\) )", " ", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


@lru_cache(maxsize=1)
def versions() -> tuple[Version, ...]:
    con = _connect()
    try:
        pages = _pages(con)
    finally:
        con.close()
    runs = _runs(pages)
    seen: dict[tuple, Version] = {}
    prev_end = 0
    for start, end in runs:
        date, sender, on_thread = _thread_meta(pages, prev_end + 1, start)
        prev_end = end
        conds, comments = _split(pages, start, end)
        v = Version(f"p{start}", start, end, date, sender, conds, comments, on_thread)
        sig = tuple((n, c.text) for n, c in sorted(conds.items()))
        # the same draft filed twice: keep the copy that sits under its own draft permit thread
        if sig not in seen or (on_thread and not seen[sig].on_draft_thread):
            seen[sig] = v
    out = list(seen.values())
    out.sort(key=lambda v: (v.thread_date or dt.date.min, -v.first_page))
    for i, v in enumerate(out, 1):
        v.n = i
        when = v.thread_date.strftime("%b %-d, %Y") if v.thread_date else "undated"
        v.label = f"Draft {i}, {when}"
    return tuple(out)


def clean_versions() -> list[Version]:
    return [v for v in versions() if not v.redline]


def latest() -> Version:
    """The newest clean draft (no margin comments, no tracked change residue)."""
    return clean_versions()[-1]


def first() -> Version:
    return clean_versions()[0]


def net_change(number: int) -> dict:
    """The condition in the first clean draft against the latest one."""
    a, b = first().conditions.get(number), latest().conditions.get(number)
    if a is None and b is None:
        return {"status": "absent"}
    if a is None:
        return {"status": "added", "old": None, "new": b}
    if b is None:
        return {"status": "removed", "old": a, "new": None}
    if a.text == b.text:
        return {"status": "same", "old": a, "new": b}
    return {"status": "changed", "old": a, "new": b, "diff": diff_html(a.text, b.text)}


@dataclass
class Thread:
    root: Comment
    replies: list[Comment]
    version: Version
    conditions: list[int]

    @property
    def outcome(self) -> str:
        last = (self.replies[-1].text if self.replies else "").lower()
        if not self.replies:
            return "open"
        if "accept" in last and not any(w in last for w in ("revised", "additional", "alternative")):
            return "accepted"
        return "countered"


STOP = set("this that with from have will been were which their there these those shall would could should please "
           "updated update added proposed propose edits edit table application accepted thank based also".split())


def _tokens(t: str) -> set[str]:
    return {w for w in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}|\d+(?:\.\d+)?", t.lower()) if w not in STOP}


def _attribute(v: Version, c: Comment, replies: list[Comment]) -> list[int]:
    """Which condition a margin comment is about.

    Comments print at the foot of the page, so the page only narrows it to the
    conditions that touch that page. Among those, pick the one sharing the most
    distinctive words with the comment thread; with no overlap, use the condition
    that ends the page body.
    """
    on_page = [n for n, cd in sorted(v.conditions.items()) if cd.page_no <= c.page_no <= cd.last_page]
    if len(on_page) <= 1:
        return on_page
    words = _tokens(" ".join([c.text] + [r.text for r in replies]))
    df: dict[str, int] = {}
    for cd in v.conditions.values():
        for w in _tokens(cd.text):
            df[w] = df.get(w, 0) + 1
    scores = {n: sum(1 / df[w] for w in words & _tokens(v.conditions[n].text)) for n in on_page}
    best = max(scores.values())
    if best <= 0:
        return [on_page[-1]]
    return [n for n in on_page if scores[n] == best][:1]


@lru_cache(maxsize=1)
def threads() -> tuple[Thread, ...]:
    """Comment threads across the redlines, keeping each one from the latest redline that has it."""
    found: dict[str, Thread] = {}
    for v in versions():
        if not v.redline:
            continue
        roots = {c.num: c for c in v.comments if c.reply_to is None}
        for c in roots.values():
            replies = [r for r in v.comments if r.reply_to == c.num]
            conds = _attribute(v, c, replies)
            key = c.text[:80].lower()
            found[key] = Thread(c, replies, v, conds)   # later redlines overwrite earlier ones
    return tuple(sorted(found.values(), key=lambda t: (t.conditions[:1] or [99], t.root.page_no)))


def threads_for(number: int) -> list[Thread]:
    return [t for t in threads() if number in t.conditions]


# ---- diffs -----------------------------------------------------------------

def diff_html(old: str, new: str) -> Markup:
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(escape(" ".join(a[i1:i2])))
        if op in ("delete", "replace"):
            out.append(Markup("<del>") + escape(" ".join(a[i1:i2])) + Markup("</del>"))
        if op in ("insert", "replace"):
            out.append(Markup("<ins>") + escape(" ".join(b[j1:j2])) + Markup("</ins>"))
    return Markup(" ").join(out)


def history(number: int) -> list[dict]:
    """Every version of one condition, oldest first, with what changed from the one before."""
    rows, prev = [], None
    for v in clean_versions():
        c = v.conditions.get(number)
        if c is None and prev is None:
            continue
        if c is None:
            rows.append({"version": v, "status": "removed", "cond": None, "diff": None})
        elif prev is None:
            rows.append({"version": v, "status": "first", "cond": c, "diff": None})
        elif prev.text == c.text:
            rows.append({"version": v, "status": "same", "cond": c, "diff": None})
        else:
            rows.append({"version": v, "status": "changed", "cond": c, "diff": diff_html(prev.text, c.text)})
        prev = c
    return rows


def changed_numbers() -> dict[int, int]:
    """Condition number -> how many times its text changed across the drafts."""
    return {n: (1 if net_change(n)["status"] in ("changed", "added") else 0) for n in latest().conditions}


def version_changes() -> list[dict]:
    """Per version: which conditions it changed, added, or dropped versus the previous draft."""
    vs, out = clean_versions(), []
    for prev, v in zip(vs, vs[1:]):
        changed = [n for n in v.conditions if n in prev.conditions and v.conditions[n].text != prev.conditions[n].text]
        added = [n for n in v.conditions if n not in prev.conditions]
        dropped = [n for n in prev.conditions if n not in v.conditions]
        out.append({"version": v, "prev": prev, "changed": changed, "added": added, "dropped": dropped})
    return out


# ---- structured design basis --------------------------------------------------

@dataclass
class Value:
    label: str
    display: str
    page_no: int
    quote: str
    extra: dict = field(default_factory=dict)


NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
EQUIP = re.compile(
    r"(?P<count>\d+|One|Two|Three|Four|Five|Six)\s+(?:General Electric \(GE\) |GE |Solar Turbines )?model\s+(?P<model>[\w.]+(?: \d+)?(?: \(\w+\))?)\s+CTGs?\s+operated in\s+(?P<mode>combined|simple) cycle mode.*?"
    r"average heat input of (?P<heat>[\d,.]+)\s*(?:million British thermal units per hour \(MMBtu/hr\)|MMBtu/hr).*?"
    r"(?:rated )?nominal capacity of (?P<mw>[\d,.]+)\s*(?:gross )?(?:megawatts \(MW\)|MW)", re.S)
LIMIT = re.compile(r"(Nitrogen oxide \(NOx\)|Carbon monoxide \(CO\)|Ammonia \(NH3\))\s+([\d.]+)\s+(\d+-hr rolling average)")
GROUP = re.compile(r"(Combined Cycle GE\s+7HA\.02|Simple Cycle GE\s+7HA\.02|Simple Cycle Solar\s+T350|Simple Cycle Solar\s+T130)")


def _page_of(v: Version, cond: Condition, snippet: str) -> tuple[int, str]:
    """The page within the condition's span holding the snippet, and the quote to cite.

    A snippet can straddle a page break; then cite its longest tail that sits on one page.
    """
    con = _connect()
    try:
        texts = con.execute("SELECT page_no, text FROM pages WHERE doc_id=? AND page_no BETWEEN ? AND ? ORDER BY page_no",
                            (DOC_ID, cond.page_no, max(cond.last_page, cond.page_no))).fetchall()
    finally:
        con.close()
    words = snippet.split()
    for k in range(len(words), 3, -1):
        part = words[len(words) - k:]
        rx = re.compile(r"\s+".join(re.escape(w) for w in part))
        for p, t in texts:
            if rx.search(t):
                return p, " ".join(part)
    return cond.page_no, snippet


@lru_cache(maxsize=1)
def design_basis() -> dict:
    v = latest()
    out: dict = {"version": v, "equipment": [], "limits": [], "mss": [], "fuel": []}
    c5 = v.conditions.get(5)
    if c5:
        for m in EQUIP.finditer(c5.text):
            cnt = m.group("count")
            n = NUM_WORDS.get(cnt.lower(), None) or int(cnt)
            page, quote = _page_of(v, c5, f"average heat input of {m.group('heat')}")
            out["equipment"].append(Value(
                f"{m.group('model')} {m.group('mode')} cycle", f"{n} x {m.group('mw')} MW", page, quote,
                {"count": n, "model": m.group("model"), "mode": m.group("mode"), "heat": m.group("heat"), "mw": m.group("mw")}))
    c6 = v.conditions.get(6)
    if c6:
        groups = list(GROUP.finditer(c6.text))
        for i, g in enumerate(groups):
            seg = c6.text[g.end(): groups[i + 1].start() if i + 1 < len(groups) else len(c6.text)]
            for lm in LIMIT.finditer(seg):
                pol = re.search(r"\((\w+)\)", lm.group(1)).group(1)
                page, quote = _page_of(v, c6, f"{lm.group(1)} {lm.group(2)} {lm.group(3)}")
                out["limits"].append(Value(" ".join(g.group(1).split()), f"{pol} {lm.group(2)} ppmvd", page, quote,
                                           {"group": " ".join(g.group(1).split()), "pollutant": pol, "ppm": lm.group(2), "avg": lm.group(3)}))
        for rx, label in [(r"Planned startups for combined cycle operation shall not exceed (\d+) minutes per startup", "Combined cycle startup, max"),
                          (r"Planned startups for simple cycle operation shall not exceed (\d+) minutes per startup", "Simple cycle startup, max"),
                          (r"Planned shutdowns shall not exceed (\d+) minutes per shutdown", "Shutdown, max"),
                          (r"ramp rate greater than (\d+) MW per minute", "Transitional load threshold")]:
            m = re.search(rx, c6.text)
            if m:
                unit = "MW/min" if "ramp" in rx else "min"
                page, quote = _page_of(v, c6, " ".join(m.group(0).split()))
                out["mss"].append(Value(label, f"{m.group(1)} {unit}", page, quote))
    c7 = v.conditions.get(7)
    if c7:
        for m in re.finditer(r"No more than ([\d.]+) (?:grains of total sulfur per 100 dry standard cubic feet \(gr S/100 dscf\)|gr S/100 dscf)(?: on an hourly basis and no more than [\d.]+ gr S/100 dscf on a 12-month rolling period)? for the ([^(]+?)(?: \(|$)", c7.text):
            page, quote = _page_of(v, c7, " ".join(m.group(0).rstrip(" (").split()))
            out["fuel"].append(Value(m.group(2).strip().replace("the ", ""), f"{m.group(1)} gr S/100 dscf", page, quote))
    return out


def topic_label(key: str) -> str:
    return next((lab for k, lab, _, _ in TOPICS if k == key), "Other")


def epc_topics() -> list[str]:
    return [k for k, _, epc, _ in TOPICS if epc]
