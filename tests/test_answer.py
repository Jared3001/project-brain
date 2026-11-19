"""The withhold checks, without calling the API."""

import pytest

from brain.answer import numbers, retrieve, verify
from brain.ingest import DB_PATH
from brain.search import Hit

PAGE = Hit("tceq-application-2026-tc", "TCEQ permit file", 1, "2026-08-06", 30, "p. 30", 0.0, "",
           "The proposed Kilby Power Plant’s nominal power generation capacity will be approximately "
           "2,869 megawatts (MW) at site conditions. These 29 turbines were added on December 22, 2025.")
IDS = {"P1": PAGE}


def parsed(text, quote, page_id="P1"):
    return {"status": "answered", "not_found_note": "",
            "sentences": [{"text": text, "citations": [{"page_id": page_id, "quote": quote}]}]}


def test_grounded_sentence_passes():
    _, cites, problems = verify(parsed("The plant is rated at 2,869 MW.", "approximately 2,869 megawatts"), IDS)
    assert problems == []
    assert cites[0].quotes == ["approximately 2,869 megawatts"]


def test_number_from_elsewhere_on_the_cited_page_passes():
    _, _, problems = verify(parsed("29 turbines were added on December 22, 2025.", "These 29 turbines were added"), IDS)
    assert problems == []


def test_invented_number_is_withheld():
    _, _, problems = verify(parsed("The plant is rated at 2,670 MW.", "approximately 2,869 megawatts"), IDS)
    assert any("2,670" in p for p in problems)


def test_quote_not_on_page_is_withheld():
    _, _, problems = verify(parsed("It is big.", "approximately 3 gigawatts"), IDS)
    assert any("quote not found" in p for p in problems)


def test_unknown_page_is_withheld():
    _, _, problems = verify(parsed("It is big.", "approximately", page_id="P9"), IDS)
    assert any("not provided" in p for p in problems)


def test_quote_matching_ignores_curly_quotes_and_line_breaks():
    _, _, problems = verify(parsed("Rated at 2,869 MW.", "Kilby Power Plant's nominal power\ngeneration capacity"), IDS)
    assert problems == []


def test_numbers_skip_single_digits_but_keep_decimals():
    assert numbers("2 units, 2.67 GW and 2,869 MW in 2026") == {"2.67", "2,869", "2026"}


@pytest.mark.skipif(not DB_PATH.exists(), reason="run ingest and record first")
def test_retrieval_caps_monthly_reports_and_finds_the_filings():
    hits = retrieve("Why do the capacity numbers disagree?")
    ercot = {h.doc_id for h in hits if h.doc_id.startswith("ercot-gis-")}
    assert len(ercot) <= 2
    assert any(h.doc_id == "tceq-application-2026-tc" and h.page_no in (30, 681) for h in hits)
