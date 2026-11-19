import sqlite3

import pytest

from brain.ingest import DB_PATH
from brain.record import Fact, build, compatible

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run `python -m brain.ingest` first")


@pytest.fixture(scope="module")
def record():
    records, problems = build()
    return {r.field: r for r in records}, problems


def f(value, qualifier="exact"):
    return Fact("x", value, "", "s", 1, "2026-01-01", "d", 1, "p. 1", "", qualifier=qualifier)


def test_compatibility_respects_qualifiers():
    assert compatible(f(13_890_275.01), f(13e6, "more_than"))
    assert not compatible(f(11_564_704.59), f(13e6, "more_than"))
    assert compatible(f(2869), f(3000, "nearly"))
    assert not compatible(f(2869), f(2670, "approx"))


def test_every_extractor_matched_and_every_fact_is_grounded(record):
    _, problems = record
    assert problems == []


def test_capacity_is_a_conflict_with_the_filing_history_kept(record):
    rec = record[0]["capacity_mw"]
    assert rec.status == "conflict"
    assert {c.display for c in rec.current} >= {"2,869 MW", "about 2.67 GW"}
    assert [h.display for h in rec.history] == ["2,595 MW", "2,476 MW"]


def test_co2e_change_is_history_not_conflict(record):
    rec = record[0]["co2e_tpy"]
    assert rec.status == "found"  # 13,890,275.01 is "more than 13 million"
    assert [h.display for h in rec.history] == ["11,564,704.59 tons/yr"]


def test_battery_cut_has_its_date_range(record):
    rec = record[0]["battery_storage_mw"]
    assert rec.current[0].display == "330 MW"
    h = rec.history[0]
    assert (h.display, h.since, h.until) == ("560 MW", "2026-02-02", "2026-07-01")


def test_unrelated_kilby_station_rows_never_enter_the_record(record):
    for rec in record[0].values():
        for fact in rec.current + rec.history:
            assert "Quantum" not in fact.display and "Haskell" not in fact.display


def test_facts_table_written():
    con = sqlite3.connect(DB_PATH)
    n = con.execute("SELECT count(*) FROM facts WHERE role='current'").fetchone()[0]
    statuses = dict(con.execute("SELECT status, count(*) FROM record GROUP BY status").fetchall())
    assert n > 20
    assert statuses.get("conflict") == 1
