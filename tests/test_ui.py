import html
import re

import pytest

from brain.ingest import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run ingest and record first")


@pytest.fixture(scope="module")
def client():
    from web.app import app
    return app.test_client()


def test_project_header_cards_and_tracks():
    from web.app import app, project_header
    with app.test_request_context("/"):
        ph = project_header()
    labels = [c["label"] for c in ph["cards"]]
    assert labels[:3] == ["Air permit", "Capacity in the permit", "Earliest projected COD"]
    assert ph["cards"][2]["value"] == "Nov 4, 2027"
    tracks = dict(ph["tracks"])
    assert [s["label"] for s in tracks["Air permit"]][0] == "Application filed"
    states = {s["label"]: s["state"] for s in tracks["ERCOT interconnection"]}
    assert states["Screening study"] == "done" and states["Full interconnection study"] == "current"
    assert ph["recent"] and ph["recent"][0]["date"]


def test_header_links_open_real_quotes(client):
    for url in ["/", "/record"]:
        page = client.get(url).data.decode()
        assert 'class="stepper"' in page
        for href in set(re.findall(r'href="(/source/[^"#]+)', page)):
            r = client.get(html.unescape(href))
            ok = b'<mark id="q">' in r.data or b"not reproduced" in r.data
            assert ok, href


def test_consistency_grid_colors_against_the_majority(client):
    page = client.get("/consistency").data.decode()
    assert 'class="grid"' in page
    row = page[page.index("<th>Semafor"):]
    row = row[:row.index("</tr>")]
    assert 'class="differ"' in row      # nearly 3 GW against the 2.67 GW most sources give
    erc = page[page.index("<th>ERCOT queue"):]
    erc = erc[:erc.index("</tr>")]
    assert "Nov 4, 2027" in erc


def test_condition_story_reads_ask_reply_final():
    from brain import permit as P
    st = P.story(24)
    kinds = [s["kind"] for s in st["steps"]]
    assert kinds[0] == "draft" and kinds[-1] == "final" and "ask" in kinds and "reply" in kinds
    assert any("12-hour shift" in x for x in st["summary"])
    assert P.story(5)["summary"] == ["44 → 46"]


def test_change_summary_counts_long_edits():
    from brain.permit import change_summary
    old = "a b c " + " ".join(f"w{i}" for i in range(20))
    new = "a x c " + " ".join(f"v{i}" for i in range(20))
    out = change_summary(old, new)
    assert out[0] == "b → x" and out[-1] == "1 longer edit"


def test_condition_page_shows_the_strip(client):
    page = client.get("/permit/24").data.decode()
    assert 'class="strip"' in page and "Changed since Draft 1" in page and "countered" in page
