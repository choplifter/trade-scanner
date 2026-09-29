"""Chain normalisation and the cache, with duck-typed fakes in the style of
test_gamma_exposure.py -- no SDK objects, no network."""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest
from alpaca.trading.enums import ContractType

from app.options.chain import (
    ContractMeta,
    build_chain_rows,
    expiries_from_contracts,
    mid_price,
    quote_from_snapshot,
)
from app.options.chain_fetch import ChainCache, fetch_contracts, strike_band

# Anchored to the clock rather than written down: ChainCache drops expiries
# that have passed, so a fixed date turns every cache test red the moment
# the calendar reaches it (which it did on 2026-09-19).
def _utc_today() -> date:
    """The same "today" ChainCache uses. Deliberately not date.today():
    that is this machine's local date, which after 20:00 ET is already
    tomorrow in Europe -- the test would pass by day and fail at night."""
    return datetime.now(timezone.utc).date()


EXPIRY = _utc_today() + timedelta(days=16)


def _meta(symbol, kind, strike, expiry=EXPIRY, oi=5, tradable=True) -> ContractMeta:
    return ContractMeta(
        symbol=symbol, underlying="SPY", root="SPY", expiry=expiry, kind=kind, strike=strike,
        open_interest=oi, tradable=tradable, close_price=None,
    )


@dataclass
class _Quote:
    bid_price: float | None
    ask_price: float | None
    bid_size: int = 3
    ask_size: int = 4


@dataclass
class _Trade:
    price: float


@dataclass
class _Greeks:
    delta: float
    gamma: float
    theta: float
    vega: float = 0.1
    rho: float = 0.0


@dataclass
class _Snap:
    latest_quote: _Quote | None
    latest_trade: _Trade | None
    greeks: _Greeks | None
    implied_volatility: float | None


def test_mid_rules():
    assert mid_price(1.0, 1.2, None) == 1.1
    assert mid_price(0, 1.2, 0.9) == 0.9  # one-sided market: fall back to last
    assert mid_price(None, None, None) is None
    assert mid_price(1.3, 1.2, 1.0) == 1.0  # crossed book is not a market


def test_quote_from_snapshot_tolerates_missing_greeks_and_no_snapshot():
    meta = _meta("SPY260918C00750000", "call", 750)
    full = quote_from_snapshot(meta, _Snap(_Quote(1.0, 1.2), _Trade(1.1), _Greeks(0.4, 0.01, -0.05), 0.18))
    assert full.mid == 1.1 and full.delta == 0.4 and full.iv == 0.18 and full.open_interest == 5
    bare = quote_from_snapshot(meta, _Snap(_Quote(1.0, 1.2), None, None, None))
    assert bare.delta is None and bare.iv is None and bare.mid == 1.1
    none = quote_from_snapshot(meta, None)
    assert none.bid is None and none.mid is None and none.symbol == meta.symbol


def test_build_chain_rows_joins_sorts_and_keeps_unquoted_strikes():
    contracts = {
        "SPY260918C00750000": _meta("SPY260918C00750000", "call", 750),
        "SPY260918P00750000": _meta("SPY260918P00750000", "put", 750),
        "SPY260918C00745000": _meta("SPY260918C00745000", "call", 745),
        "SPY260925C00745000": _meta("SPY260925C00745000", "call", 745, expiry=date(2026, 9, 25)),
    }
    snapshots = {"SPY260918C00750000": _Snap(_Quote(1.0, 1.2), None, None, None)}
    rows = build_chain_rows(contracts, snapshots, EXPIRY)
    assert [r.strike for r in rows] == [745, 750]
    assert rows[0].call is not None and rows[0].call.mid is None and rows[0].put is None
    assert rows[1].call.mid == 1.1 and rows[1].put is not None


def test_volume_is_merged_per_contract_and_absent_means_none_traded():
    """The snapshot carries no volume, so it arrives from a separate day-bar
    call keyed by symbol. A contract missing from that set did not trade --
    a zero -- while a chain built without the call at all is unknown."""
    contracts = {
        "SPY260918C00750000": _meta("SPY260918C00750000", "call", 750),
        "SPY260918P00750000": _meta("SPY260918P00750000", "put", 750),
    }
    snapshots = {"SPY260918C00750000": _Snap(_Quote(1.0, 1.2), None, None, None)}

    rows = build_chain_rows(contracts, snapshots, EXPIRY, {"SPY260918C00750000": 1234})
    assert rows[0].call.volume == 1234
    assert rows[0].put.volume == 0, "asked for and not traded"

    unknown = build_chain_rows(contracts, snapshots, EXPIRY)
    assert unknown[0].call.volume is None and unknown[0].put.volume is None


def test_expiries_are_counted_and_dated():
    today = _utc_today()
    soon = today + timedelta(days=2)
    contracts = [
        _meta("a", "call", 1), _meta("b", "put", 1),
        _meta("c", "call", 1, expiry=soon),
        _meta("d", "call", 1, expiry=today - timedelta(days=5)),  # already expired: dropped
    ]
    out = expiries_from_contracts(contracts, today)
    assert [(e.expiry, e.dte, e.contract_count) for e in out] == [
        (soon, 2, 1),
        (EXPIRY, 16, 2),
    ]


# --- fetch + cache with fakes --------------------------------------------------


@dataclass
class _Contract:
    symbol: str
    open_interest: int | None
    type: object
    strike_price: float
    expiration_date: date = EXPIRY
    underlying_symbol: str = "SPY"
    root_symbol: str = "SPY"
    tradable: bool = True
    close_price: str | None = "1.05"


@dataclass
class _Page:
    option_contracts: list
    next_page_token: str | None


class _FakeTrading:
    def __init__(self):
        self.requests = []

    def get_option_contracts(self, request):
        self.requests.append(request)
        if request.page_token is None:
            return _Page([_Contract("SPY260918C00750000", None, ContractType.CALL, 750.0)], "p2")
        return _Page([_Contract("SPY260918P00750000", 7, ContractType.PUT, 750.0)], None)


class _FakeOptions:
    def __init__(self):
        self.calls = 0

    def get_option_chain(self, request):
        self.calls += 1
        return {"SPY260918C00750000": _Snap(_Quote(1.0, 1.2), None, _Greeks(0.5, 0.01, -0.03), 0.2)}


class _FakeClients:
    def __init__(self):
        self.trading = _FakeTrading()
        self.options = _FakeOptions()
        self.options_feed = "opra"


def test_fetch_contracts_paginates_and_sends_strike_bounds_as_strings():
    clients = _FakeClients()
    contracts = asyncio.run(fetch_contracts(clients, "SPY", _utc_today(), _utc_today() + timedelta(days=60), 675.0, 825.0))
    assert set(contracts) == {"SPY260918C00750000", "SPY260918P00750000"}
    assert contracts["SPY260918C00750000"].open_interest == 0  # None -> 0
    assert contracts["SPY260918P00750000"].open_interest == 7
    assert len(clients.trading.requests) == 2
    assert clients.trading.requests[0].strike_price_gte == "675.0"
    assert clients.trading.requests[1].page_token == "p2"


def test_chain_cache_serves_within_ttl_and_refetches_after():
    clients = _FakeClients()
    clock = {"t": 1000.0}

    async def spot(_symbol):
        return 750.0

    cache = ChainCache(clients, spot, now=lambda: clock["t"])
    first = asyncio.run(cache.chain("spy", EXPIRY))
    assert first.underlying == "SPY" and first.spot == 750.0 and first.feed == "opra"
    assert [r.strike for r in first.rows] == [750.0]
    assert first.rows[0].call.delta == 0.5 and first.rows[0].put.mid is None
    assert clients.options.calls == 1

    clock["t"] += 10  # inside the 15s chain TTL
    asyncio.run(cache.chain("SPY", EXPIRY))
    assert clients.options.calls == 1

    clock["t"] += 10  # past it: one more chain fetch, contracts still cached
    asyncio.run(cache.chain("SPY", EXPIRY))
    assert clients.options.calls == 2
    assert len(clients.trading.requests) == 2  # the two pages of the single contracts fetch


def test_chain_cache_refuses_an_unknown_expiry():
    clients = _FakeClients()

    async def spot(_symbol):
        return 750.0

    cache = ChainCache(clients, spot)
    try:
        asyncio.run(cache.chain("SPY", EXPIRY + timedelta(days=1)))
    except LookupError as exc:
        assert "expiry" in str(exc)
    else:
        raise AssertionError("expected LookupError")
    # The far strip was looked at too (one fetch, its own window and band).
    far = [r for r in clients.trading.requests if r.expiration_date_gte != clients.trading.requests[0].expiration_date_gte]
    assert far and float(far[0].strike_price_gte) < 750.0 * 0.7 and float(far[0].strike_price_lte) < 750.0 * 1.2


class _FarTrading(_FakeTrading):
    """The near window has the September contract; the far window a LEAPS."""

    def get_option_contracts(self, request):
        self.requests.append(request)
        if date.fromisoformat(request.expiration_date_gte) > EXPIRY:
            return _Page([_Contract("SPY271217C00650000", 3, ContractType.CALL, 650.0, expiration_date=date(2027, 12, 17))], None)
        return super().get_option_contracts(request)


def test_chain_cache_serves_a_far_expiry_from_the_far_strip():
    clients = _FakeClients()
    clients.trading = _FarTrading()

    async def spot(_symbol):
        return 750.0

    cache = ChainCache(clients, spot)
    _spot, _contracts, near = asyncio.run(cache.contracts("SPY"))
    assert [e.expiry for e in near] == [EXPIRY]
    _spot, far = asyncio.run(cache.far_expiries("SPY", 270, 540))
    assert [e.expiry for e in far] == [date(2027, 12, 17)] and far[0].dte > 365
    chain = asyncio.run(cache.chain("SPY", date(2027, 12, 17)))
    assert chain.expiry == date(2027, 12, 17) and [r.strike for r in chain.rows] == [650.0]
    # That one expiry was fetched as its own window; the window is cached
    # like the near strip, so asking again fetches nothing.
    before = len(clients.trading.requests)
    asyncio.run(cache.far_expiries("SPY", 270, 540))
    asyncio.run(cache.chain("SPY", date(2027, 12, 17)))
    assert len(clients.trading.requests) == before
    assert clients.trading.requests[-1].expiration_date_gte == "2027-12-17" and clients.trading.requests[-1].expiration_date_lte == "2027-12-17"


def test_the_board_lists_far_expiries_from_a_sliver_of_strikes():
    clients = _FakeClients()
    clients.trading = _FarTrading()

    async def spot(_symbol):
        return 750.0

    cache = ChainCache(clients, spot)
    _spot, board = asyncio.run(cache.board_expiries("SPY"))
    assert [e.expiry for e in board] == [date(2027, 12, 17)]
    request = clients.trading.requests[-1]
    # A sliver around the spot (1.5 % a side), from just past the strip out to the LEAPS.
    assert float(request.strike_price_gte) == pytest.approx(738.75) and float(request.strike_price_lte) == pytest.approx(761.25)
    assert date.fromisoformat(request.expiration_date_gte) > EXPIRY
    assert (date.fromisoformat(request.expiration_date_lte) - date.fromisoformat(request.expiration_date_gte)).days > 900
    # Cached: a second ask fetches nothing.
    before = len(clients.trading.requests)
    asyncio.run(cache.board_expiries("SPY"))
    assert len(clients.trading.requests) == before


# --- the strike band the reader asks for ---------------------------------------


def test_the_default_band_is_ten_percent_either_side_with_a_dollar_floor():
    assert strike_band(750.0) == (675.0, 825.0)
    # A cheap name: 10 % of $12 is $1.20, which on $0.50 strikes is nothing
    # to build a spread from, so the dollar floor takes over.
    assert strike_band(12.0) == (7.0, 17.0)


def test_a_wider_band_reaches_the_wings_the_default_cannot():
    """The reason this parameter exists: a condor on a $1,050 name wants
    strikes a third of the way down, and ±10 % stops at 945."""
    narrow_low, narrow_high = strike_band(1050.0)
    wide_low, wide_high = strike_band(1050.0, 0.50, 0.50)
    assert (narrow_low, narrow_high) == (945.0, 1155.0)
    assert (wide_low, wide_high) == (525.0, 1575.0)


def test_a_band_wider_than_the_spot_never_asks_for_a_strike_below_zero():
    low, _high = strike_band(4.0, 2.0, 2.0)
    assert low > 0


def test_the_width_reaches_alpaca_and_is_not_rounded_away():
    clients = _FakeClients()

    async def spot(_symbol):
        return 1050.0

    cache = ChainCache(clients, spot)
    try:
        asyncio.run(cache.chain("SPY", EXPIRY, 0.50))
    except LookupError:
        pass  # the fake lists one strike; the request is what is under test
    first = clients.trading.requests[0]
    assert first.strike_price_gte == "525.0" and first.strike_price_lte == "1575.0"


def test_a_narrow_cache_entry_is_not_served_to_a_wide_request():
    """The bug this key exists to prevent: fetch ±10 %, then ask for ±50 %
    and get the narrow rows back from the cache -- the wings would vanish
    again, silently, and only for as long as the TTL."""
    clients = _FakeClients()

    async def spot(_symbol):
        return 750.0

    cache = ChainCache(clients, spot, now=lambda: 1000.0)
    asyncio.run(cache.chain("SPY", EXPIRY))
    before = len(clients.trading.requests)
    asyncio.run(cache.chain("SPY", EXPIRY, 0.50))
    assert len(clients.trading.requests) > before, "a wider band must go and fetch"


def test_the_chain_says_which_band_it_was_fetched_with():
    """So "nothing above 825" can be told apart from "the fetch stopped at
    825" -- the reader has to know which of the two they are looking at."""
    clients = _FakeClients()

    async def spot(_symbol):
        return 750.0

    chain = asyncio.run(ChainCache(clients, spot).chain("SPY", EXPIRY))
    assert (chain.strike_low, chain.strike_high) == (675.0, 825.0)
    assert chain.to_dict()["strike_high"] == 825.0
