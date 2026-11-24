import re

import pytest

from brain.ingest import DB_PATH
from web.app import EMAIL, _mask, app

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run ingest and record first")


@pytest.fixture
def client():
    return app.test_client()


def test_card_and_changes_render(client):
    assert client.get("/").status_code == 200
    page = client.get("/changes").data.decode()
    assert "560" in page and "330" in page
    assert "CC" in page and "GT" in page                 # Part 6 technology code change
    assert "Cancelled from the queue (500 MW Solar)" in page                   # Part 5 solar


def test_capacity_changes_follow_filing_order(client):
    page = client.get("/changes").data.decode()
    text = re.sub(r"<[^>]+>", " ", page)
    # newest first: the update to 2,869 is listed above the restatement to 2,476
    assert text.index("2,476 MW") < text.index("2,869 MW")


def test_source_view_highlights_quote(client):
    r = client.get("/source/tceq-application-2026-tc/681?q=total nominal net output of 2,476 megawatts")
    assert b'<mark id="q">' in r.data


def test_press_text_is_not_reproduced(client):
    r = client.get("/source/semafor-2026-10-01/1?q=nearly 3 gigawatts")
    assert b"not reproduced" in r.data
    assert b'<pre class="page">' not in r.data


def test_private_emails_are_masked_and_agency_ones_kept():
    sub = lambda s: EMAIL.sub(_mask, s)
    assert sub("To: someone@gmail.com") == "To: [email withheld]"
    assert sub("Huy.Pham@tceq.texas.gov") == "Huy.Pham@tceq.texas.gov"
    assert sub("a.person@consultingfirm.com") == "[email withheld]@consultingfirm.com"


def test_daily_cap_blocks_new_questions(monkeypatch):
    import web.app as w
    monkeypatch.setattr(w, "_cached", lambda q: None)
    monkeypatch.setattr(w, "_spent_today", lambda: 99.0)
    called = []
    monkeypatch.setattr("brain.answer.ask", lambda q: called.append(q))
    with w.app.test_request_context("/ask"):
        w._recent.clear()
        a = w.guarded_ask("A brand new question?")
    assert a.status == "error" and "budget" in a.note and not called


def test_hourly_limit_per_visitor(monkeypatch):
    import web.app as w
    monkeypatch.setattr(w, "_cached", lambda q: None)
    monkeypatch.setattr(w, "_spent_today", lambda: 0.0)
    monkeypatch.setattr("brain.answer.ask", lambda q: w.__dict__.get("Answer", None) or q)
    with w.app.test_request_context("/ask", headers={"X-Forwarded-For": "203.0.113.9"}):
        w._recent.clear()
        for _ in range(w.PER_IP_PER_HOUR):
            w.guarded_ask("q")
        a = w.guarded_ask("q")
    assert a.status == "error" and "hourly" in a.note


def test_eval_page_shows_runs_and_every_question(client):
    page = client.get("/eval").data.decode()
    for label in ("Baseline", "v1", "v2", "Held out"):
        assert label in page
    assert page.count('class="case"') == 87
    assert "[answered]" not in page
