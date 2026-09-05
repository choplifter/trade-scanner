"""compute_gex's sign convention/edge cases (pure), plus _fetch_contracts'
None-open-interest coercion and pagination against a fake TradingClient --
no real Alpaca calls.
"""

import asyncio

import pytest
from dataclasses import dataclass
from datetime import date, datetime, timezone

from alpaca.trading.enums import ContractType

from app.market_data.gamma_exposure import (
    OptionRow,
    StrikeGex,
    _fetch_contracts,
    call_wall,
    compute_expected_move,
    compute_gex,
    compute_near_expiry_gex,
    gamma_flip_strike,
    nearest_expiry,
    put_wall,
    top_walls,
)

_CALL = ContractType.CALL
_PUT = ContractType.PUT

_AS_OF = datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc)


def test_calls_are_positive_and_puts_are_negative():
    reading = compute_gex(
        "SPY",
        spot_price=100.0,
        contracts=[(0.01, 1000, True, 100.0), (0.01, 1000, False, 100.0)],
        as_of=_AS_OF,
    )
    assert reading.call_gex > 0
    assert reading.put_gex < 0
    # Equal gamma/OI on both sides at this spot -> they cancel exactly.
    assert reading.net_gex == 0
    assert reading.contracts_used == 2


def test_empty_contract_list_yields_zero_not_an_error():
    reading = compute_gex("SPY", spot_price=100.0, contracts=[], as_of=_AS_OF)
    assert reading.net_gex == 0
    assert reading.call_gex == 0
    assert reading.put_gex == 0
    assert reading.contracts_used == 0
    assert reading.by_strike == []


def test_dollar_gamma_formula():
    # gamma * OI * 100 * spot**2 * 0.01, calls positive.
    reading = compute_gex(
        "SPY", spot_price=500.0, contracts=[(0.02, 50, True, 500.0)], as_of=_AS_OF
    )
    expected = 0.02 * 50 * 100 * 500.0**2 * 0.01
    assert reading.call_gex == expected
    assert reading.net_gex == expected


def test_by_strike_aggregates_same_strike_and_sorts_ascending():
    reading = compute_gex(
        "SPY",
        spot_price=100.0,
        contracts=[
            (0.01, 100, True, 110.0),  # two contracts at the same strike...
            (0.01, 100, False, 110.0),  # ...must land in one StrikeGex row
            (0.02, 50, True, 90.0),
        ],
        as_of=_AS_OF,
    )
    assert [row.strike for row in reading.by_strike] == [90.0, 110.0]

    row_110 = reading.by_strike[1]
    call_110 = 0.01 * 100 * 100 * 100.0**2 * 0.01
    put_110 = -(0.01 * 100 * 100 * 100.0**2 * 0.01)
    assert row_110.call_gex == call_110
    assert row_110.put_gex == put_110
    assert row_110.net_gex == call_110 + put_110

    row_90 = reading.by_strike[0]
    call_90 = 0.02 * 50 * 100 * 100.0**2 * 0.01
    assert row_90.call_gex == call_90
    assert row_90.put_gex == 0
    assert row_90.net_gex == call_90

    # The two views (aggregate vs. per-strike) are derived from the exact
    # same per-contract values, so they must always agree.
    assert sum(row.net_gex for row in reading.by_strike) == reading.net_gex


def test_top_walls_picks_largest_magnitude_and_sorts_by_strike():
    rows = [
        StrikeGex(strike=100.0, net_gex=-500.0, call_gex=0.0, put_gex=-500.0),
        StrikeGex(strike=110.0, net_gex=2000.0, call_gex=2000.0, put_gex=0.0),
        StrikeGex(strike=90.0, net_gex=-3000.0, call_gex=0.0, put_gex=-3000.0),
        StrikeGex(strike=120.0, net_gex=100.0, call_gex=100.0, put_gex=0.0),
    ]

    walls = top_walls(rows, n=3)

    # -3000 (strike 90), 2000 (strike 110), -500 (strike 100) are the three
    # largest by |net_gex| -- the +100 at strike 120 must be excluded --
    # returned strike-ascending, not magnitude-ascending.
    assert [w.strike for w in walls] == [90.0, 100.0, 110.0]


def test_top_walls_n_larger_than_input_returns_everything():
    rows = [StrikeGex(strike=100.0, net_gex=1.0, call_gex=1.0, put_gex=0.0)]
    assert top_walls(rows, n=3) == rows


def test_call_wall_and_put_wall_pick_largest_by_side():
    rows = [
        StrikeGex(strike=90.0, net_gex=-1000.0, call_gex=500.0, put_gex=-1500.0),
        StrikeGex(strike=100.0, net_gex=3000.0, call_gex=3000.0, put_gex=0.0),
        StrikeGex(strike=110.0, net_gex=-4000.0, call_gex=0.0, put_gex=-4000.0),
    ]
    assert call_wall(rows).strike == 100.0
    assert put_wall(rows).strike == 110.0


def test_call_wall_and_put_wall_none_for_empty_profile():
    assert call_wall([]) is None
    assert put_wall([]) is None


def test_gamma_flip_strike_interpolates_the_zero_crossing():
    # Cumulative net_gex ascending: -1000, then -1000+3000=2000 -- crosses
    # zero between strike 90 and strike 100, 1/3 of the way across.
    rows = [
        StrikeGex(strike=90.0, net_gex=-1000.0, call_gex=0.0, put_gex=-1000.0),
        StrikeGex(strike=100.0, net_gex=3000.0, call_gex=3000.0, put_gex=0.0),
    ]
    flip = gamma_flip_strike(rows)
    assert flip == 90.0 + (1000.0 / 3000.0) * (100.0 - 90.0)


def test_gamma_flip_strike_none_when_no_crossing():
    rows = [
        StrikeGex(strike=90.0, net_gex=1000.0, call_gex=1000.0, put_gex=0.0),
        StrikeGex(strike=100.0, net_gex=2000.0, call_gex=2000.0, put_gex=0.0),
    ]
    assert gamma_flip_strike(rows) is None


def test_gamma_flip_strike_none_with_fewer_than_two_strikes():
    assert gamma_flip_strike([]) is None
    assert gamma_flip_strike([StrikeGex(strike=100.0, net_gex=1.0, call_gex=1.0, put_gex=0.0)]) is None


@dataclass
class _Contract:
    symbol: str
    open_interest: int | None
    type: object
    strike_price: float
    expiration_date: date = date(2026, 10, 16)


@dataclass
class _Page:
    option_contracts: list
    next_page_token: str | None


class _FakeTrading:
    """Two pages of results -- proves _fetch_contracts follows
    next_page_token until it runs out, and that a None open_interest
    coerces to 0 rather than propagating as a gap."""

    def __init__(self):
        self.requests = []

    def get_option_contracts(self, request):
        self.requests.append(request)
        if request.page_token is None:
            return _Page(
                option_contracts=[
                    _Contract("SPY_A", open_interest=5, type=_CALL, strike_price=400.0),
                    _Contract("SPY_B", open_interest=None, type=_PUT, strike_price=420.0),
                ],
                next_page_token="page2",
            )
        return _Page(
            option_contracts=[_Contract("SPY_C", open_interest=10, type=_CALL, strike_price=450.0)],
            next_page_token=None,
        )


class _FakeClients:
    def __init__(self, trading):
        self.trading = trading


def test_fetch_contracts_paginates_and_treats_none_oi_as_zero():
    clients = _FakeClients(_FakeTrading())

    contracts = asyncio.run(
        _fetch_contracts(clients, "SPY", date(2026, 10, 1), date(2026, 11, 1), 400.0, 500.0)
    )

    assert contracts == {
        "SPY_A": (5, True, 400.0, date(2026, 10, 16)),
        "SPY_B": (0, False, 420.0, date(2026, 10, 16)),
        "SPY_C": (10, True, 450.0, date(2026, 10, 16)),
    }
    assert len(clients.trading.requests) == 2
    assert clients.trading.requests[1].page_token == "page2"


# --- the nearest expiry and the expected move --------------------------------

from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _row(expiry, strike, is_call, *, oi=1000, gamma=None, bid=None, ask=None):
    return OptionRow(f"X{strike:g}{'C' if is_call else 'P'}", expiry, strike, is_call, oi, gamma, bid, ask)


def test_nearest_expiry_is_today_while_the_session_trades():
    today = date(2026, 9, 4)
    expiries = [today, date(2026, 9, 8), date(2026, 9, 11)]
    at_noon = datetime(2026, 9, 4, 12, 0, tzinfo=_ET)
    after_close = datetime(2026, 9, 4, 16, 30, tzinfo=_ET)

    assert nearest_expiry(expiries, at_noon) == today
    assert nearest_expiry(expiries, after_close) == date(2026, 9, 8)
    # Saturday: the next listed expiry.
    assert nearest_expiry(expiries, datetime(2026, 9, 5, 10, 0, tzinfo=_ET)) == date(2026, 9, 8)
    assert nearest_expiry([], at_noon) is None


def test_near_expiry_gex_solves_gamma_from_the_quote_when_the_feed_has_none():
    # A 0DTE chain: Alpaca hands back no greeks, only quotes.
    today = date(2026, 9, 4)
    now = datetime(2026, 9, 4, 10, 0, tzinfo=_ET)
    rows = [
        _row(today, 100.0, True, bid=1.00, ask=1.10),
        _row(today, 100.0, False, bid=1.00, ask=1.10),
        _row(today, 105.0, True, bid=0.05, ask=0.10, oi=5000),
        _row(date(2026, 9, 11), 100.0, True, gamma=0.02),  # another expiry: ignored
    ]

    near = compute_near_expiry_gex(100.0, today, rows, now)

    assert near is not None
    assert near.is_today and near.dte == 0
    assert near.source == "solved"
    assert near.contracts_used == 3
    assert [r.strike for r in near.by_strike] == [100.0, 105.0]
    # Equal call and put at the money cancel; the 105 call leaves the net positive.
    assert near.net_gex > 0


def test_near_expiry_gex_prefers_feed_gammas_and_says_so():
    expiry = date(2026, 9, 8)
    now = datetime(2026, 9, 5, 10, 0, tzinfo=_ET)
    rows = [_row(expiry, 100.0, True, gamma=0.03), _row(expiry, 95.0, False, gamma=0.02)]

    near = compute_near_expiry_gex(100.0, expiry, rows, now)

    assert near is not None
    assert near.source == "alpaca"
    assert not near.is_today and near.dte == 3
    assert near.contracts_used == 2


def test_near_expiry_gex_is_none_without_a_usable_contract():
    expiry = date(2026, 9, 8)
    now = datetime(2026, 9, 5, 10, 0, tzinfo=_ET)
    assert compute_near_expiry_gex(100.0, expiry, [_row(expiry, 100.0, True)], now) is None


def test_expected_move_is_the_atm_straddle_and_one_sigma_scales_it():
    expiry = date(2026, 9, 8)
    now = datetime(2026, 9, 5, 10, 0, tzinfo=_ET)
    rows = [
        _row(expiry, 99.0, True, bid=1.90, ask=2.10),
        _row(expiry, 99.0, False, bid=0.90, ask=1.10),
        _row(expiry, 100.0, True, bid=1.40, ask=1.60),   # nearest spot, both sides quoted
        _row(expiry, 100.0, False, bid=1.40, ask=1.60),
        _row(expiry, 101.0, True, bid=1.00, ask=1.20),   # no put quote: not a candidate
    ]

    em = compute_expected_move(100.2, expiry, rows, now)

    assert em is not None
    assert em.strike == 100.0
    assert em.straddle_mid == 3.0 and em.move == 3.0
    assert em.one_sigma == pytest.approx(3.0 * 1.2533, abs=1e-3)
    assert (em.low, em.high) == (97.2, 103.2)
    assert em.dte == 3


def test_expected_move_needs_a_strike_quoted_on_both_sides():
    expiry = date(2026, 9, 8)
    now = datetime(2026, 9, 5, 10, 0, tzinfo=_ET)
    assert compute_expected_move(100.0, expiry, [_row(expiry, 100.0, True, bid=1, ask=1.2)], now) is None
