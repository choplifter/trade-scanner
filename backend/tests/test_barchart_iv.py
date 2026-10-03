"""Seeding the IV history from Barchart's daily export: what is read from
the file, and that a seeded day never displaces one of ours."""

import asyncio
import sqlite3
from datetime import date

import pytest

from app.options.barchart_iv import newest_session, parse_history, symbol_from_filename
from app.options.iv_history_store import SOURCE_BARCHART, IvHistoryStore

AS_OF = date(2026, 10, 2)

EXPORT = """Date,"Imp Vol","1D IV Chg","IV Rank","IV Pctl","P/C Vol","Options Vol","P/C OI","Total OI",Last
2026-10-02,13.41%,-0.73%,15.50%,28%,1.22,14272657,2.10,17706667,769.64
2026-10-01,14.14%,+0.01%,20.08%,44%,1.15,15035010,2.13,17326993,763.99
2026-06-15,,+0.01%,20.08%,44%,1.15,15035010,2.13,17326993,700.00
2025-10-03,18.00%,+0.33%,10.52%,32%,1.52,7912763,2.60,15626019,666.84
2025-09-19,31.74%,+0.44%,9.52%,28%,1.30,7904917,3.00,19407079,663.7
"Downloaded from Barchart.com as of 10-03-2026 02:31am CDT"
"""


def test_the_filename_names_the_symbol_and_a_browser_duplicate_too():
    assert symbol_from_filename("spy_options-overview-history-10-03-2026.csv") == "SPY"
    assert symbol_from_filename("qqq_options-overview-history-10-03-2026 (1).csv") == "QQQ"
    assert symbol_from_filename("short-iron-condor-option-screener-10-03-2026.csv") is None


def test_the_history_is_cut_to_the_52_weeks_barchart_ranks_against():
    readings = parse_history(EXPORT, as_of=AS_OF)
    # The September 2025 row is outside the year (it is what made LQD's rank
    # read 34 % against Barchart's 90 %); the blank IV and the footer go too.
    assert readings == [(date(2025, 10, 3), 0.18), (date(2026, 10, 1), 0.1414), (date(2026, 10, 2), 0.1341)]
    assert newest_session(EXPORT) == AS_OF


def _store(tmp_path) -> IvHistoryStore:
    store = IvHistoryStore(str(tmp_path / "h.sqlite3"))
    store._init_schema_sync()
    return store


def test_a_table_from_before_the_source_column_is_migrated_and_its_rows_are_ours(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE option_iv_history (symbol TEXT NOT NULL, session_date TEXT NOT NULL, atm_iv REAL NOT NULL, "
            "dte INTEGER NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY (symbol, session_date))"
        )
        conn.execute("INSERT INTO option_iv_history VALUES ('SPY', '2026-10-01', 0.14, 49, 'x')")
    store = IvHistoryStore(str(path))
    store._init_schema_sync()
    store._init_schema_sync()  # and a second start does not try again

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT source FROM option_iv_history").fetchall() == [("recorder",)]


def test_seeding_fills_the_gaps_and_leaves_our_own_readings_alone(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.record("SPY", date(2026, 10, 1), 0.145, 49))

    added = store.seed_sync("spy", parse_history(EXPORT, as_of=AS_OF), source=SOURCE_BARCHART, dte=30)

    assert added == 2, "10-01 is ours already"
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute("SELECT session_date, atm_iv, source FROM option_iv_history ORDER BY session_date").fetchall()
    assert rows == [("2025-10-03", 0.18, "barchart"), ("2026-10-01", 0.145, "recorder"), ("2026-10-02", 0.1341, "barchart")]


def test_a_reading_the_rank_never_reads_does_not_keep_a_seed_out(tmp_path):
    store = _store(tmp_path)
    # A 1-DTE chain opened that day: written once, before the band guarded
    # the write, and ignored by every read since.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("INSERT INTO option_iv_history VALUES ('SPY', '2026-10-02', 0.30, 1, 'x', 'recorder')")

    added = store.seed_sync("SPY", [(AS_OF, 0.1341)], source=SOURCE_BARCHART, dte=30)

    assert added == 1
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT atm_iv, dte, source FROM option_iv_history").fetchone() == (0.1341, 30, "barchart")


def test_our_reading_replaces_a_seeded_day_and_the_rank_reads_both(tmp_path):
    store = _store(tmp_path)
    store.seed_sync("SPY", [(date(2026, 9, d), 0.10 + d / 1000) for d in range(1, 30)], source=SOURCE_BARCHART, dte=30)

    asyncio.run(store.record("SPY", date(2026, 9, 29), 0.20, 49))

    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT atm_iv, source FROM option_iv_history WHERE session_date='2026-09-29'").fetchone() == (0.20, "recorder")
    rank, samples = asyncio.run(store.rank("SPY", 0.20))
    assert samples == 29 and rank is not None and rank.percent == pytest.approx(100.0)


def test_a_seed_outside_the_comparable_band_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _store(tmp_path).seed_sync("SPY", [(AS_OF, 0.13)], source=SOURCE_BARCHART, dte=7)
