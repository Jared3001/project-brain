"""Full text retrieval over ingested pages.

bm25 over SQLite FTS5 with porter stemming. Plain English questions are turned
into an OR query of their content words, so a page does not need every word to
match. Start simple; the eval decides whether embeddings are needed.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from brain.ingest import DB_PATH

STOP = set("""
a an and are as at be by did do does for from has have how in is it its of on or
that the this to was were what when where which who why will with about into
than then there their they them it's whats much many any kilby project plant
""".split())


@dataclass
class Hit:
    doc_id: str
    title: str
    tier: int
    published: str
    page_no: int
    locator: str
    score: float
    snippet: str
    text: str


def to_fts_query(question: str) -> str:
    """'What is the total MW?' -> '"total" OR "mw"' (quoted, so FTS syntax in user text is inert)."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-]*", question.lower())
    terms = []
    for w in words:
        w = w.strip(".-")
        if w and w not in STOP and w not in terms:
            terms.append(w)
    return " OR ".join(f'"{t}"' for t in terms)


def search(question: str, k: int = 8, db_path: Path = DB_PATH,
           kinds: list[str] | None = None) -> list[Hit]:
    q = to_fts_query(question)
    if not q:
        return []
    con = sqlite3.connect(db_path)
    sql = """
      SELECT p.doc_id, d.title, d.tier, d.published, p.page_no, p.locator,
             bm25(pages_fts) AS score,
             snippet(pages_fts, 0, '[', ']', ' ... ', 24),
             p.text
      FROM pages_fts
      JOIN pages p ON p.rowid = pages_fts.rowid
      JOIN documents d ON d.id = p.doc_id
      WHERE pages_fts MATCH ?
    """
    args: list = [q]
    if kinds:
        sql += f" AND d.kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    sql += " ORDER BY score LIMIT ?"
    args.append(k)
    rows = con.execute(sql, args).fetchall()
    con.close()
    return [Hit(*r) for r in rows]


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "nominal generation capacity"
    print(f"query: {to_fts_query(question)}\n")
    for h in search(question):
        print(f"{h.score:7.2f}  T{h.tier}  {h.doc_id} {h.locator}")
        print(f"         {h.snippet.replace(chr(10), ' ')}\n")
