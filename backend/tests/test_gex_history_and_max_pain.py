"""Max pain over one expiry, and the net-GEX percentile that gives a
reading something to be measured against."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.market_data.gamma_exposure import OptionRow, max_pain
from app.market_data.gex_history_store import GexHistoryStore

EXPIRY = date(2026, 9, 18)


def _row(strike: float, is_call: bool, oi: int) -> OptionRow:
    return OptionRow(
        symbol="X", expiry=EXPIRY, strike=strike, is_call=is_call, open_interest=oi, gamma=None, bid=None, ask=None
    )


# --- max pain ---------------------------------------------------------------


def test_max_pain_is_where_the_open_contracts_are_worth_least():
    # Everything sits on 100: at 100 both sides expire worthless, and any
    # other strike puts that whole block in the money on one side.
    rows = [
        _row(95, True, 10),
        _row(100, True, 500),
        _row(105, True, 10),
        _row(95, False, 10),
        _row(100, False, 500),
        _row(105, False, 10),
    ]
    assert max_pain(rows) == 100


def test_max_pain_settles_on_the_strike_carrying_the_weight():
    """Five thousand calls at 100 against a handful of puts at 105: every
    dollar above 100 costs the call side five thousand, so the total is
    smallest right at 100, not between the two strikes.

    Note which way the pull runs -- a heavy strike attracts, it does not
    repel. A wall of puts *below* the money pays nothing while price stays
    above it and therefore moves the answer not at all."""
    rows = [_row(100, True, 5000), _row(105, False, 10)]
    assert max_pain(rows) == 100

    # The same weight on the put side pulls the other way.
    assert max_pain([_row(100, False, 5000), _row(95, True, 10)]) == 100


def test_max_pain_ignores_strikes_nobody_holds_and_needs_two():
    assert max_pain([_row(100, True, 0), _row(105, False, 0)]) is None
    assert max_pain([_row(100, True, 50)]) is None
    assert max_pain([]) is None


# --- the net-GEX percentile ---------------------------------------------------


def _store(tmp_path) -> GexHistoryStore:
    store = GexHistoryStore(str(tmp_path / "gex.sqlite3"))
    asyncio.run(store.init_schema())
    return store


def test_a_reading_has_no_rank_until_there_is_a_range_to_place_it_in(tmp_path):
    store = _store(tmp_path)
    for day in range(1, 11):
        asyncio.run(store.record("SPY", date(2026, 9, day), net_gex=-1e9 * day, spot_price=700.0))

    rank, samples = asyncio.run(store.rank("SPY", -5e9))
    assert rank is None  # ten sessions is a coincidence, not a range
    assert samples == 10


def test_a_percentile_once_the_history_is_long_enough(tmp_path):
    store = _store(tmp_path)
    for day in range(1, 26):
        asyncio.run(store.record("SPY", date(2026, 8, day), net_gex=float(day), spot_price=700.0))

    rank, samples = asyncio.run(store.rank("SPY", 25.0))
    assert samples == 25
    assert rank is not None and rank.percent == pytest.approx(100.0)
    assert (rank.low, rank.high) == (1.0, 25.0)

    low, _ = asyncio.run(store.rank("SPY", 1.0))
    assert low is not None and low.percent == pytest.approx(0.0)


def test_one_row_per_session_with_the_last_write_winning(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.record("SPY", date(2026, 9, 1), net_gex=1.0, spot_price=700.0))
    asyncio.run(store.record("SPY", date(2026, 9, 1), net_gex=2.0, spot_price=701.0))

    assert asyncio.run(store.history("SPY")) == [2.0]


def test_an_absent_reading_has_no_rank_rather_than_a_zero(tmp_path):
    store = _store(tmp_path)
    assert asyncio.run(store.rank("SPY", None)) == (None, 0)
