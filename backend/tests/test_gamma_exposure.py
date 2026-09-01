"""compute_gex's sign convention/edge cases (pure), plus _fetch_contracts'
None-open-interest coercion and pagination against a fake TradingClient --
no real Alpaca calls.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone

from alpaca.trading.enums import ContractType

from app.market_data.gamma_exposure import StrikeGex, _fetch_contracts, compute_gex, top_walls

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


@dataclass
class _Contract:
    symbol: str
    open_interest: int | None
    type: object
    strike_price: float


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
        "SPY_A": (5, True, 400.0),
        "SPY_B": (0, False, 420.0),
        "SPY_C": (10, True, 450.0),
    }
    assert len(clients.trading.requests) == 2
    assert clients.trading.requests[1].page_token == "page2"
