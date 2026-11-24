import html
import re

import pytest

from brain.ingest import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run ingest first")


@pytest.fixture(scope="module")
def P():
    from brain import permit
    return permit


def test_finds_six_distinct_drafts_in_date_order(P):
    vs = P.versions()
    assert len(vs) == 6                                   # a seventh copy is an exact duplicate
    dates = [v.thread_date for v in vs]
    assert dates == sorted(dates)
    assert [v.redline for v in vs] == [False, False, True, True, True, False]
    assert P.first().first_page == 532 and P.latest().first_page == 95


def test_every_draft_splits_into_forty_numbered_conditions(P):
    for v in P.versions():
        assert sorted(v.conditions) == list(range(1, 41)), v.label


def test_comments_are_not_left_in_condition_text(P):
    for v in P.versions():
        for c in v.conditions.values():
            assert "Commented [" not in c.text and "Formatted:" not in c.text


def test_equipment_matches_the_46_turbine_fleet(P):
    eq = P.design_basis()["equipment"]
    assert [(v.extra["model"], v.extra["count"]) for v in eq] == [
        ("7HA.02", 2), ("7HA.02", 3), ("Titan 350 (T350)", 12), ("Titan 130 (T130)", 29)]
    assert sum(v.extra["count"] for v in eq) == 46


def test_emission_limits_table(P):
    lim = {(v.extra["group"], v.extra["pollutant"]): v.extra["ppm"] for v in P.design_basis()["limits"]}
    assert lim[("Combined Cycle GE 7HA.02", "NOx")] == "2.0"
    assert lim[("Simple Cycle GE 7HA.02", "NOx")] == "2.5"
    assert lim[("Simple Cycle Solar T130", "CO")] == "2.5"
    assert len(lim) == 12


def test_negotiation_threads_keep_tceq_replies(P):
    ts = P.threads()
    turbine = next(t for t in ts if "44 to 46" in t.root.text)
    assert turbine.conditions == [5] and turbine.outcome == "accepted"
    assert turbine.replies[0].org == "TCEQ"
    avo = next(t for t in ts if "Entergy" in t.root.text)
    assert avo.conditions == [24] and avo.outcome == "countered"


def test_net_change_shows_the_avo_check_compromise(P):
    nc = P.net_change(24)
    assert nc["status"] == "changed"
    assert "12-hour shift" in nc["new"].text


def test_lens_pages_render_and_every_permit_quote_is_on_its_page():
    from web.app import app
    c = app.test_client()
    for url in ["/epc", "/permit", "/permit?topic=limits", "/permit/5", "/permit/24"]:
        page = c.get(url)
        assert page.status_code == 200, url
        for href in set(re.findall(r'href="(/source/tceq-application-2026-tc/[^"#]+)', page.data.decode())):
            r = c.get(html.unescape(href))
            assert b'<mark id="q">' in r.data, href
    assert c.get("/permit/99").status_code == 404
