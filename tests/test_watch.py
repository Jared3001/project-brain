import datetime as dt
import hashlib

import pytest

from brain.ingest import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run ingest and record first")


def _md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def replay():
    from brain import watch
    before = _md5(DB_PATH)
    base, cand, new = watch.build_replay(dt.date(2026, 7, 15))
    d = watch.diff(watch.snapshot(base, dt.date(2026, 7, 16)), watch.snapshot(cand, dt.date(2026, 11, 23)))
    return {"diff": d, "new": new, "before": before}


def test_replay_never_touches_the_live_database(replay):
    assert _md5(DB_PATH) == replay["before"]


def test_replay_sees_the_documents_published_after_the_day(replay):
    ids = {n["doc_id"] for n in replay["new"]}
    assert "ercot-gis-2026-10" in ids and "tceq-pending-2026-10-30" in ids and "semafor-2026-10-01" in ids


def test_replay_catches_the_battery_cut_and_the_permit_status(replay):
    rec = {r["field"]: r for r in replay["diff"]["record"]}
    assert "560 MW" in rec["battery_storage_mw"]["gone"]
    assert any(a["display"] == "330 MW" for a in rec["battery_storage_mw"]["added"])
    assert "Administrative Complete Application" in rec["air_permit_status"]["gone"]


def test_replay_flags_deadline_status_changes(replay):
    dl = {x["key"]: x for x in replay["diff"]["deadlines"]}
    assert dl["fid"]["before"] == "upcoming" and dl["fid"]["after"]["status"] == "at_risk"


def test_identical_snapshots_diff_to_nothing():
    from brain import watch
    s = watch.snapshot(DB_PATH, dt.date(2026, 11, 23))
    assert watch.is_empty(watch.diff(s, s))


def test_markdown_for_a_quiet_run():
    from brain import watch
    md = watch.markdown({"ran_at": "2026-11-23T10:00:00", "mode": "live", "checked": ["ERCOT"], "new": [],
                         "problems": []})
    assert "Nothing new" in md


def test_promote_refuses_paths_outside_the_watch_folder(tmp_path):
    from brain import watch
    with pytest.raises(SystemExit):
        watch.promote(str(tmp_path / "x.db"))


def test_watch_page_renders():
    from web.app import app
    assert app.test_client().get("/watch").status_code == 200
