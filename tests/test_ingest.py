import sqlite3
from pathlib import Path

import pytest

from brain.ingest import DB_PATH, extract_ercot, extract_text
from brain.registry import ROOT, load_registry, validate
from brain.search import search, to_fts_query

HAVE_DB = DB_PATH.exists()
needs_db = pytest.mark.skipif(not HAVE_DB, reason="run `python -m brain.ingest` first")


def test_registry_is_sound():
    reg = load_registry()
    assert validate(reg) == []
    ids = [d.id for d in reg.documents]
    assert "tceq-application-2026-tc" in ids
    assert any(i.startswith("ercot-gis-") for i in ids)


def test_ercot_ids_name_the_report_month_not_the_publish_date():
    reg = load_registry()
    doc = next(d for d in reg.documents if d.path.name.startswith("2026-11-02"))
    assert doc.id == "ercot-gis-2026-10"
    assert "October 2026" in doc.title


def test_fts_query_quotes_terms_and_drops_stopwords():
    assert to_fts_query("What is the total MW?") == '"total" OR "mw"'
    # FTS operators typed by a user are inert once quoted
    assert to_fts_query('capacity" OR NEAR(') == '"capacity" OR "near"'
    assert to_fts_query("the of and") == ""


def test_text_sections_stop_at_site_navigation(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("Real paragraph one.\n\nReal paragraph two.\n\nRelated content\n\nUnrelated headline")
    pages = extract_text(f)
    assert len(pages) == 1
    assert "Unrelated" not in pages[0].text


def test_ercot_extractor_finds_every_kilby_part():
    path = ROOT / "corpus/ercot/2026-11-02_ercot_gis_report.xlsx"
    if not path.exists():
        pytest.skip("ERCOT reports not downloaded")
    text = "\n".join(p.text for p in extract_ercot(path))
    for inr in ("27INR0572", "28INR0457", "28INR0459", "29INR0199"):
        assert inr in text
    assert "Capacity (MW): 330" in text  # BESS after the August 2026 cut


@needs_db
def test_pdf_page_numbers_are_the_printed_pages():
    """No pages dropped: page_no must run 1..N with N the PDF page count."""
    con = sqlite3.connect(DB_PATH)
    n, lo, hi = con.execute(
        "SELECT count(*), min(page_no), max(page_no) FROM pages WHERE doc_id='tceq-application-2026-tc'"
    ).fetchone()
    assert (n, lo, hi) == (1100, 1, 1100)


@needs_db
def test_search_finds_the_updated_capacity_page():
    hits = search("What is the nominal generation capacity in megawatts?", k=5)
    assert any("2,869" in h.text and h.doc_id == "tceq-application-2026-tc" for h in hits)


@needs_db
def test_search_finds_the_ppa_term_in_the_company_release():
    hits = search("How long is the Microsoft power purchase agreement?", k=5)
    assert any(h.doc_id == "chevron-release-2026-06-22" and "20-year" in h.text for h in hits)
