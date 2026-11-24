import datetime as dt
import html
import re

import pytest

from brain.ingest import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run ingest and record first")

DAY = dt.date(2026, 11, 23)


@pytest.fixture(scope="module")
def M():
    from brain import commitments
    return commitments


def _by_label(items, text):
    return next(i for i in items if text in i.label)


def test_statuses_on_a_fixed_day(M):
    items = M.dated(DAY)
    assert _by_label(items, "Application filed").status == "done"
    assert _by_label(items, "Technically complete").status == "done"          # estimate, and the pending list shows it reached
    assert _by_label(items, "Estimated final issuance").status == "past_due"  # Oct 15 estimate, still technically complete
    assert _by_label(items, "Final investment decision").status == "at_risk"  # Semafor says it could slip
    assert _by_label(items, "Part 1 Small Block").date == dt.date(2027, 11, 4)


def test_statuses_move_with_the_day(M):
    early = M.dated(dt.date(2026, 10, 1))
    assert _by_label(early, "Estimated final issuance").status == "due_soon"


def test_timeline_is_in_date_order(M):
    dates = [i.date for i in M.dated(DAY)]
    assert dates == sorted(dates)


def test_every_permit_obligation_matches(M):
    found, misses = M.triggered()
    assert misses == [] and len(found) == len(M.TRIGGERED)
    windows = {t.window for t in found}
    assert "At least 45 days before sampling" in windows and "Within 15 days" in windows


def test_pages_render_and_cite_real_quotes():
    from web.app import app
    c = app.test_client()
    for url in ["/", "/record", "/commitments"]:
        r = c.get(url)
        assert r.status_code == 200, url
        for href in set(re.findall(r'href="(/source/[^"#]+)', r.data.decode())):
            rr = c.get(html.unescape(href))
            assert b'<mark id="q">' in rr.data or b"not reproduced" in rr.data, href
    home = c.get("/").data.decode()
    assert "What do you work on?" in home and "EPC and engineering" in home
