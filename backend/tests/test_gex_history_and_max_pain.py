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


# --- a contract set that reports no open interest ------------------------------


def test_gex_of_a_set_with_no_open_interest_is_exactly_zero():
    """Why fetch_gex has to refuse it rather than publish it: every dollar
    of gamma here is gamma x open interest, so a set in which nothing is
    reported comes out at exactly zero -- and zero does not read as a gap,
    it reads as "dealers are flat"."""
    from datetime import datetime, timezone

    from app.market_data.gamma_exposure import compute_gex

    now = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)
    contracts = [(0.02, 0, True, 100.0), (0.02, 0, False, 100.0), (0.015, 0, True, 105.0)]
    reading = compute_gex("X", 100.0, contracts, now)
    assert reading.net_gex == 0.0
    assert reading.open_interest_used == 0
    # Indistinguishable from a genuinely balanced book, which is the point.
    assert reading.contracts_used == 3


def test_fetch_gex_answers_nothing_when_not_one_contract_reports_open_interest():
    """Seen on a single GOOGL expiry on 2026-10-01: every quote tradable
    and two-sided, open interest unset on all of them. One expiry is
    survivable here because this aggregates many; all of them silent is
    not, and a reading of zeros would be a claim about positioning nobody
    reported."""
    from app.market_data import gamma_exposure

    assert asyncio.run(_fetch_with(gamma_exposure, open_interest=0)) is None


def test_fetch_gex_still_answers_when_some_contracts_report_it():
    """Zeros interleaved with real values are a real "nothing open on this
    strike" -- _fetch_contracts' own note records observing exactly that."""
    from app.market_data import gamma_exposure

    reading = asyncio.run(_fetch_with(gamma_exposure, open_interest=400))
    assert reading is not None and reading.open_interest_used > 0


async def _fetch_with(module, *, open_interest: int):
    """fetch_gex over two contracts whose open interest is `open_interest`,
    with every network call stubbed."""
    import datetime as _dt

    today = _dt.datetime.now(_dt.timezone.utc).astimezone(module.ET).date()
    expiry = today + _dt.timedelta(days=30)
    contracts = {
        "X1": (open_interest, True, 100.0, expiry),
        "X2": (open_interest, False, 100.0, expiry),
    }
    gammas = {"X1": (0.02, 1.0, 1.1), "X2": (0.02, 1.0, 1.1)}

    async def _spot(_clients, _symbol):
        return 100.0

    async def _fetch_contracts(*_args, **_kwargs):
        return contracts

    async def _fetch_gammas(*_args, **_kwargs):
        return gammas

    originals = (module._spot_price, module._fetch_contracts, module._fetch_gammas)
    module._spot_price, module._fetch_contracts, module._fetch_gammas = _spot, _fetch_contracts, _fetch_gammas
    try:
        return await module.fetch_gex(object(), "X")  # type: ignore[arg-type]
    finally:
        module._spot_price, module._fetch_contracts, module._fetch_gammas = originals
