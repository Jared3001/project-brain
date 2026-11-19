"""Build data/brain.db from the registered corpus.

Every document becomes rows in `pages`. What a "page" is depends on the source:
  PDF               one row per printed page (locator "p. 12")
  extracted text    paragraph aligned sections of about 300 words ("section 3")
  ERCOT GIS xlsx    one row per sheet that mentions Kilby, rows rendered as
                    "column: value" lines ("sheet Project Details - Large Gen")
  TCEQ pending list the Kilby row of that day's snapshot ("Kilby row")

The database is rebuilt from scratch each run: the corpus is the source of
truth and the index is disposable.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import openpyxl
import pymupdf

from brain.registry import ROOT, Document, load_registry, validate

DB_PATH = ROOT / "data" / "brain.db"
SECTION_WORDS = 300
LOW_TEXT_CHARS = 40  # below this a PDF page probably needs OCR
OCR_DPI = 200
TESSDATA = next((p for p in (Path("/opt/homebrew/share/tessdata"), Path("/usr/share/tesseract-ocr/5/tessdata"),
                             Path("/usr/share/tessdata")) if p.exists()), None)
KILBY = re.compile(r"kilby|energy forge", re.I)

SCHEMA_SQL = """
CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  tier INTEGER NOT NULL,
  publisher TEXT NOT NULL,
  published TEXT NOT NULL,
  url TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  n_pages INTEGER NOT NULL,
  notes TEXT
);
CREATE TABLE pages (
  rowid INTEGER PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(id),
  page_no INTEGER NOT NULL,
  locator TEXT NOT NULL,
  text TEXT NOT NULL,
  n_chars INTEGER NOT NULL,
  low_text INTEGER NOT NULL DEFAULT 0,
  ocr INTEGER NOT NULL DEFAULT 0,
  UNIQUE (doc_id, page_no)
);
CREATE VIRTUAL TABLE pages_fts USING fts5(
  text, content='pages', content_rowid='rowid', tokenize='porter unicode61'
);
CREATE TABLE ingest_runs (
  ran_at TEXT NOT NULL,
  n_documents INTEGER NOT NULL,
  n_pages INTEGER NOT NULL,
  seconds REAL NOT NULL
);
"""


@dataclass
class Page:
    page_no: int
    locator: str
    text: str
    low_text: bool = False
    ocr: bool = False


# ---------- extractors ----------

def extract_pdf(path: Path) -> list[Page]:
    """Text layer first; OCR only pages that have images and almost no text.

    Pages that stay empty are kept (low_text=1) so the coverage report can say
    how much of a document the brain could not read.
    """
    pages = []
    with pymupdf.open(path) as doc:
        for i, pg in enumerate(doc, start=1):
            text = _clean(pg.get_text("text"))
            ocr = False
            if len(text) < LOW_TEXT_CHARS and pg.get_images() and TESSDATA:
                tp = pg.get_textpage_ocr(dpi=OCR_DPI, full=True, tessdata=str(TESSDATA))
                ocr_text = _clean(pg.get_text("text", textpage=tp))
                if len(ocr_text) > len(text):
                    text, ocr = ocr_text, True
            pages.append(Page(i, f"p. {i}", text, low_text=len(text) < LOW_TEXT_CHARS, ocr=ocr))
    return pages


JUNK_LINE = re.compile(r"Raven\.config|^Share$|^Skip to|cookie", re.I)


def extract_text(path: Path) -> list[Page]:
    """Split extracted web text into ~300 word sections on paragraph breaks."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", path.read_text(errors="ignore"))]
    paras = [p for p in paras if p and not JUNK_LINE.search(p)]
    for i, p in enumerate(paras):
        if p.lower() in ("related content", "related articles", "more from"):
            paras = paras[:i]  # site navigation from here down
            break
    sections, cur, words = [], [], 0
    for p in paras:
        n = len(p.split())
        if cur and words + n > SECTION_WORDS:
            sections.append("\n\n".join(cur))
            cur, words = [], 0
        cur.append(p)
        words += n
    if cur:
        sections.append("\n\n".join(cur))
    return [Page(i, f"section {i}", _clean(s)) for i, s in enumerate(sections, start=1)]


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    return str(v).strip()


def extract_ercot(path: Path) -> list[Page]:
    """One page per sheet containing Kilby rows, each row as 'column: value' lines."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    pages = []
    for ws in wb.worksheets:
        header = None
        blocks = []
        for row in ws.iter_rows(values_only=True):
            cells = [_cell(c) for c in row]
            if header is None and any(c.upper() == "INR" for c in cells):
                header = [c or f"column {i + 1}" for i, c in enumerate(cells)]
                continue
            if not KILBY.search(" ".join(cells)):
                continue
            names = header or [f"column {i + 1}" for i in range(len(cells))]
            lines = [f"{h}: {v}" for h, v in zip(names, cells) if v]
            blocks.append("\n".join(lines))
        if blocks:
            n = len(pages) + 1
            text = f"Sheet: {ws.title}\n\n" + "\n\n".join(blocks)
            pages.append(Page(n, f"sheet {ws.title}", text))
    wb.close()
    return pages


def extract_tceq_pending(path: Path) -> list[Page]:
    """The Kilby row of TCEQ's pending NSR permit list, with the table headers."""
    s = path.read_text(errors="ignore")
    rows = re.findall(r"(?is)<tr[^>]*>.*?</tr>", s)

    def cells(row: str) -> list[str]:
        found = re.findall(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>", row)
        return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in found]

    header = next((cells(r) for r in rows if "<th" in r.lower()), None)
    hits = [cells(r) for r in rows if KILBY.search(r) or "181895" in r]
    if not hits:
        return []
    blocks = []
    for c in hits:
        names = header if header and len(header) == len(c) else [f"column {i + 1}" for i in range(len(c))]
        blocks.append("\n".join(f"{h}: {v}" for h, v in zip(names, c) if v))
    return [Page(1, "Kilby row", "\n\n".join(blocks))]


def extract(doc: Document) -> list[Page]:
    if doc.kind == "queue_report":
        return extract_ercot(doc.path)
    if doc.kind == "status_snapshot":
        return extract_tceq_pending(doc.path)
    if doc.path.suffix == ".pdf":
        return extract_pdf(doc.path)
    if doc.path.suffix == ".txt":
        return extract_text(doc.path)
    raise ValueError(f"no extractor for {doc.rel}")


def _clean(text: str) -> str:
    text = text.replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- build ----------

def build(db_path: Path = DB_PATH, only: set[str] | None = None) -> dict:
    reg = load_registry()
    problems = validate(reg)
    if problems:
        raise SystemExit("registry problems:\n  " + "\n  ".join(problems))

    started = time.time()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA_SQL)

    report = {}
    for doc in reg.documents:
        if only and doc.id not in only:
            continue
        if not doc.path.exists():
            print(f"skip {doc.id}: {doc.rel} not present (not redistributed)", file=sys.stderr)
            continue
        pages = extract(doc)
        if doc.path.suffix != ".pdf":
            pages = [p for p in pages if p.text]
        if not any(p.text for p in pages):
            raise SystemExit(f"{doc.id}: extracted no text from {doc.rel}")
        con.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (doc.id, doc.title, doc.kind, doc.tier, doc.publisher, doc.published.isoformat(),
             doc.url, doc.rel, _sha256(doc.path), len(pages), doc.notes),
        )
        con.executemany(
            "INSERT INTO pages (doc_id, page_no, locator, text, n_chars, low_text, ocr) VALUES (?,?,?,?,?,?,?)",
            [(doc.id, p.page_no, p.locator, p.text, len(p.text), int(p.low_text), int(p.ocr)) for p in pages],
        )
        report[doc.id] = {"pages": len(pages), "low_text": sum(p.low_text for p in pages),
                          "ocr": sum(p.ocr for p in pages)}

    con.execute("INSERT INTO pages_fts(pages_fts) VALUES ('rebuild')")
    n_pages = sum(r["pages"] for r in report.values())
    con.execute(
        "INSERT INTO ingest_runs VALUES (?,?,?,?)",
        (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), len(report), n_pages,
         round(time.time() - started, 1)),
    )
    con.commit()
    con.close()
    tmp.replace(db_path)
    return report


if __name__ == "__main__":
    t = time.time()
    rep = build()
    width = max(len(k) for k in rep)
    for doc_id, r in rep.items():
        flag = "".join([f"  {r['ocr']} OCR" if r["ocr"] else "",
                        f"  {r['low_text']} unreadable" if r["low_text"] else ""])
        print(f"{doc_id:<{width}}  {r['pages']:>5} pages{flag}")
    print(f"\n{len(rep)} documents, {sum(r['pages'] for r in rep.values())} pages, "
          f"{time.time() - t:.1f}s -> {DB_PATH.relative_to(ROOT)}")
    sys.exit(0)
