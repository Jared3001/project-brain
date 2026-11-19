"""Answer a question from the corpus, or say it is not in the documents.

1. Retrieve the top pages (brain.search).
2. Ask Claude for an answer as JSON: sentences, each with page citations and a
   verbatim quote per citation, or status "not_in_documents".
3. Check in code, never trusting the model:
     - every cited page was one of the pages it was given
     - every quote appears on its cited page (whitespace tolerant)
     - every number in a sentence appears on one of that sentence's cited
       pages (quote or surrounding page text) or in a cited page's
       publication date
   Any failure withholds the whole answer and says why.
4. Log the question, outcome, tokens and cost to data/questions.db, which
   survives re-ingest.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import anthropic

from brain.registry import ROOT
from brain.ingest import DB_PATH
from brain.search import STOP, Hit, search

MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.00 / 1e6, 20.00 / 1e6   # USD per token, Opus 5.5
PER_QUESTION_CAP_USD = 0.25                     # refuse to run if the prompt alone would exceed this
K_PAGES = 8
PAGE_CHARS = 6000                               # long OCR or table pages are cut, and the cut is marked
LOG_DB = ROOT / "data" / "questions.db"

SYSTEM = """You answer questions about the Kilby Power Plant (Project Kilby, Energy Forge One LLC, Reeves County, Texas) using only the pages provided.

Rules:
- Use only the provided pages. If they do not answer the question, set status to "not_in_documents" and leave sentences empty. Do not use outside knowledge, and do not guess.
- Every sentence must cite at least one page by its id, with a quote copied character for character from that page. Quotes should be short (under 40 words) and contain the fact the sentence relies on.
- Every number you write, including dates, must appear on one of that sentence's cited pages, written the same way (keep "2,869" as "2,869", "2.67 gigawatts" as "2.67"). Do not compute totals, differences or percentages.
- When sources disagree, say so and cite both. Prefer tier 1 (regulatory filings) over tier 2 (company), tier 3 (press) and tier 4 (analysis), but do not hide a disagreement.
- When values changed over time, give the dates the pages show.
- Some pages quote other permits or projects (for example BACT comparison tables, or the unrelated Haskell County "Kilby Station"). Do not attribute those to Project Kilby.
- Two to five sentences. Plain language."""

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "not_in_documents"]},
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"page_id": {"type": "string"}, "quote": {"type": "string"}},
                            "required": ["page_id", "quote"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["text", "citations"],
                "additionalProperties": False,
            },
        },
        "not_found_note": {"type": "string"},
    },
    "required": ["status", "sentences", "not_found_note"],
    "additionalProperties": False,
}


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip().strip('"').strip("'")


@dataclass
class Citation:
    n: int
    page_id: str
    doc_id: str
    page_no: int
    locator: str
    title: str
    tier: int
    quotes: list[str] = field(default_factory=list)

    @property
    def quote(self) -> str:
        """All quotes from this page, joined the way the source viewer highlights them."""
        return " ... ".join(self.quotes)


@dataclass
class Answer:
    question: str
    status: str                  # answered | not_in_documents | withheld | error
    sentences: list[dict] = field(default_factory=list)   # {"text", "cites": [n, ...]}
    citations: list[Citation] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    searched: list[dict] = field(default_factory=list)
    note: str = ""
    draft: list[dict] = field(default_factory=list)       # withheld text, kept in the log only
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = MODEL


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[str]:
    """Numbers as written, minus trailing punctuation; single digits are ignored (counts like 'two' vs '2' noise)."""
    out = set()
    for m in NUM.findall(text):
        m = m.rstrip(".,")
        if len(m.replace(",", "").replace(".", "")) >= 2 or "." in m:
            out.add(m)
    return out


# Words that point a question at a record field, beyond the field's own label.
FIELD_WORDS = {
    "capacity_mw": {"capacity", "megawatts", "mw", "gw", "gigawatts", "size", "big", "output"},
    "turbine_fleet": {"turbines", "turbine", "7ha", "titan", "units"},
    "battery_storage_mw": {"battery", "batteries", "bess", "storage"},
    "developer": {"developer", "applicant", "owns", "owner"},
    "sponsors": {"sponsor", "sponsors", "partners", "chevron", "joulent", "engine"},
    "offtaker": {"offtaker", "customer", "buyer", "microsoft"},
    "ppa_term_years": {"ppa", "contract", "term", "agreement"},
    "turbine_suppliers": {"supplier", "suppliers", "vernova", "caterpillar", "oem"},
    "location": {"where", "location", "located", "county", "site"},
    "interconnection_point": {"interconnection", "substation", "poi", "connect", "grid"},
    "queue_entries": {"queue", "ercot", "inr", "interconnection"},
    "first_power": {"cod", "first", "online", "operation", "operational", "when"},
    "fid_status": {"fid", "investment", "decision"},
    "air_permit_status": {"permit", "status", "tceq", "approved", "issued"},
    "permit_milestones": {"permit", "milestones", "timeline", "filed", "meeting", "deficiency"},
    "co2e_tpy": {"co2", "co2e", "carbon", "emissions", "greenhouse", "ghg"},
    "nox_controls": {"nox", "controls", "scr", "catalyst", "pollution"},
    "water_source": {"water", "groundwater", "brackish"},
}
PER_DOC = {"queue_report": 1, "status_snapshot": 1}   # near duplicate monthly files: one page each, two files max
MAX_SAME_KIND = {"queue_report": 2, "status_snapshot": 2}


def retrieve(question: str, k: int = K_PAGES) -> list[Hit]:
    """Record guided first, then full text, with near duplicates capped.

    1. Fields whose words match the question contribute the pages their facts
       were read from (already verified on the record card).
    2. bm25 fills the rest, at most PER_DOC pages per monthly report and
       MAX_SAME_KIND reports of one kind, so 14 similar ERCOT files cannot
       crowd out the filings.
    """
    terms = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in STOP}
    con = sqlite3.connect(DB_PATH)
    kinds = dict(con.execute("SELECT id, kind FROM documents"))
    picked: list[Hit] = []
    seen: set[tuple[str, int]] = set()

    def add(h: Hit) -> bool:
        key = (h.doc_id, h.page_no)
        if key in seen:
            return False
        kind = kinds.get(h.doc_id, "")
        same_doc = sum(1 for p in picked if p.doc_id == h.doc_id)
        same_kind_docs = {p.doc_id for p in picked if kinds.get(p.doc_id) == kind}
        if kind in PER_DOC and same_doc >= PER_DOC[kind]:
            return False
        if kind in MAX_SAME_KIND and h.doc_id not in same_kind_docs and len(same_kind_docs) >= MAX_SAME_KIND[kind]:
            return False
        if same_doc >= 3:
            return False
        seen.add(key)
        picked.append(h)
        return True

    fields = [f for f, words in FIELD_WORDS.items() if terms & words]
    if fields:
        rows = con.execute(
            f"""SELECT DISTINCT f.doc_id, f.page_no, f.locator, f.tier, f.as_of, d.title, d.published, p.text
                FROM facts f JOIN documents d ON d.id = f.doc_id
                JOIN pages p ON p.doc_id = f.doc_id AND p.page_no = f.page_no
                WHERE f.field IN ({','.join('?' * len(fields))})
                ORDER BY f.tier, f.as_of DESC""", fields).fetchall()
        for doc_id, page_no, locator, tier, as_of, title, published, text in rows:
            if len(picked) >= k - 2:
                break   # leave room for full text hits
            add(Hit(doc_id, title, tier, published, page_no, locator, 0.0, "", text))
    con.close()
    for h in search(question, k=60):
        if len(picked) >= k:
            break
        add(h)
    return picked


def build_prompt(question: str, hits: list[Hit]) -> tuple[str, dict[str, Hit]]:
    ids = {}
    blocks = []
    for i, h in enumerate(hits, start=1):
        pid = f"P{i}"
        ids[pid] = h
        text = h.text if len(h.text) <= PAGE_CHARS else h.text[:PAGE_CHARS] + "\n[page cut here]"
        blocks.append(
            f'<page id="{pid}" document="{h.title}" location="{h.locator}" tier="{h.tier}" '
            f'published="{h.published}">\n{text}\n</page>'
        )
    return "\n\n".join(blocks) + f"\n\nQuestion: {question}", ids


def verify(parsed: dict, ids: dict[str, Hit]) -> tuple[list[dict], list[Citation], list[str]]:
    problems, sentences, citations = [], [], []
    index: dict[str, int] = {}
    for si, s in enumerate(parsed.get("sentences", []), start=1):
        cites = []
        quotes = []
        cited_pages: list[Hit] = []
        if not s["citations"]:
            problems.append(f"sentence {si} has no citation")
        for c in s["citations"]:
            hit = ids.get(c["page_id"])
            if hit is None:
                problems.append(f"sentence {si} cites {c['page_id']}, which was not provided")
                continue
            if norm(c["quote"]) not in norm(hit.text):
                problems.append(f"sentence {si}: quote not found on {hit.doc_id} {hit.locator}")
                continue
            key = c["page_id"]
            if key not in index:
                index[key] = len(citations) + 1
                citations.append(Citation(index[key], c["page_id"], hit.doc_id, hit.page_no, hit.locator,
                                          hit.title, hit.tier))
            cit = citations[index[key] - 1]
            if c["quote"] not in cit.quotes:
                cit.quotes.append(c["quote"])
            if index[key] not in cites:
                cites.append(index[key])
            quotes.append(c["quote"])
            cited_pages.append(hit)
        allowed: set[str] = set()
        for q in quotes:
            allowed |= numbers(q)
        for h in cited_pages:
            allowed |= numbers(h.text) | numbers(h.published.replace("-", " "))
            allowed |= {str(int(x)) for x in h.published.split("-")}   # 2026-06-22 -> 2026, 6, 22
        plain = {a.replace(",", "") for a in allowed}
        for n in numbers(s["text"]):
            if n not in allowed and n.replace(",", "") not in plain:
                problems.append(f"sentence {si}: number {n} is not on its cited pages")
        sentences.append({"text": s["text"], "cites": cites})
    return sentences, citations, problems


def ask(question: str, client: anthropic.Anthropic | None = None, k: int = K_PAGES, log: bool = True) -> Answer:
    hits = retrieve(question, k=k)
    searched = [{"doc_id": h.doc_id, "locator": h.locator, "page_no": h.page_no, "title": h.title} for h in hits]
    ans = Answer(question, "not_in_documents", searched=searched)
    if not hits:
        ans.note = "No page matched the question's words."
        return _log(ans) if log else ans

    prompt, ids = build_prompt(question, hits)
    est = len(prompt) / 3.5 * PRICE_IN
    if est > PER_QUESTION_CAP_USD:
        ans.status, ans.note = "error", f"Prompt would cost about ${est:.2f}, above the ${PER_QUESTION_CAP_USD} cap."
        return _log(ans) if log else ans

    load_env()
    client = client or anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIStatusError as e:
        ans.status, ans.note = "error", f"API error {e.status_code}"
        return _log(ans) if log else ans
    except anthropic.APIConnectionError:
        ans.status, ans.note = "error", "Could not reach the API."
        return _log(ans) if log else ans

    ans.model = resp.model
    ans.input_tokens = resp.usage.input_tokens
    ans.output_tokens = resp.usage.output_tokens
    ans.cost_usd = round(ans.input_tokens * PRICE_IN + ans.output_tokens * PRICE_OUT, 5)

    if resp.stop_reason == "refusal":
        ans.status, ans.note = "withheld", "The model declined to answer."
        return _log(ans) if log else ans
    if resp.stop_reason == "max_tokens":
        ans.status, ans.note = "withheld", "The answer was cut off before it finished."
        return _log(ans) if log else ans
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        ans.status, ans.note = "withheld", "The model's answer was not valid JSON."
        return _log(ans) if log else ans

    if parsed["status"] == "not_in_documents":
        ans.note = parsed.get("not_found_note", "")
        return _log(ans) if log else ans

    sentences, citations, problems = verify(parsed, ids)
    if problems:
        ans.status, ans.problems, ans.draft = "withheld", problems, sentences
        ans.note = "A draft answer failed the page check, so it is not shown."
    elif not sentences:
        ans.status, ans.note = "not_in_documents", parsed.get("not_found_note", "")
    else:
        ans.status, ans.sentences, ans.citations = "answered", sentences, citations
    return _log(ans) if log else ans


def _log(ans: Answer) -> Answer:
    LOG_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(LOG_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS questions (
        asked_at TEXT, question TEXT, status TEXT, n_citations INTEGER, problems TEXT,
        model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, answer_json TEXT)""")
    con.execute("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?)", (
        dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), ans.question, ans.status,
        len(ans.citations), json.dumps(ans.problems), ans.model, ans.input_tokens, ans.output_tokens,
        ans.cost_usd, json.dumps(asdict(ans)),
    ))
    con.commit()
    con.close()
    return ans


if __name__ == "__main__":
    a = ask(" ".join(sys.argv[1:]) or "What is the plant's generating capacity?")
    print(f"[{a.status}] ${a.cost_usd:.4f}  ({a.input_tokens} in / {a.output_tokens} out, {a.model})")
    for s in a.sentences:
        print(" ", s["text"], "".join(f"[{n}]" for n in s["cites"]))
    for c in a.citations:
        print(f"  [{c.n}] T{c.tier} {c.doc_id} {c.locator}: " + " | ".join(c.quotes))
    for p in a.problems:
        print("  problem:", p)
    if a.note:
        print("  note:", a.note)
