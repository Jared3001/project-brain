import pytest

from brain.ingest import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run ingest and record first")


@pytest.fixture(scope="module")
def C():
    from brain import claims
    return claims


def test_every_spec_still_matches_its_document(C):
    found, misses = C.claims()
    assert misses == ()
    assert len(found) >= 30


def test_every_quote_is_on_its_page(C):
    import re
    import sqlite3
    con = sqlite3.connect(DB_PATH)
    for c in C.claims()[0]:
        text = con.execute("SELECT text FROM pages WHERE doc_id=? AND page_no=?", (c.doc_id, c.page_no)).fetchone()[0]
        for part in re.split(r"\s*\.\.\.\s*", c.quote):   # record quotes join separate parts with "..."
            rx = r"\s+".join(re.escape(w) for w in part.split())
            assert re.search(rx, text), (c.topic, c.doc_id, part)


def test_verdicts(C):
    v = {f.topic: f.verdict for f in C.findings()}
    assert v["capacity"] == "disagree"          # 2.67 GW headline vs 2,869 MW permit vs queue sum
    assert v["first_power"] == "disagree"       # ERCOT Part 1 COD Nov 2027 vs "2028" everywhere else
    assert v["grid"] == "tension"
    assert v["ownership"] == "changed"          # wholly owned (Jun 22) then 50/50 (Jul 1)
    assert v["co2"] == "consistent"
    assert v["economy"] == "single"


def test_findings_are_ranked_worst_first(C):
    ranks = [f.rank for f in C.findings()]
    assert ranks == sorted(ranks)


def test_capacity_counts_the_headline_sources(C):
    f = next(f for f in C.findings() if f.topic == "capacity")
    assert f.detail[0].startswith("7 sources say 2.67 GW; the first was Chevron")


def test_page_renders():
    from web.app import app
    r = app.test_client().get("/consistency")
    assert r.status_code == 200 and b"Sources disagree" in r.data
