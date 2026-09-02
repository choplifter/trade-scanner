"""Chain normalisation and the cache, with duck-typed fakes in the style of
test_gamma_exposure.py -- no SDK objects, no network."""

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta

from alpaca.trading.enums import ContractType

from app.options.chain import (
    ContractMeta,
    build_chain_rows,
    expiries_from_contracts,
    mid_price,
    quote_from_snapshot,
)
from app.options.chain_fetch import ChainCache, fetch_contracts

EXPIRY = date(2026, 9, 18)


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


def test_expiries_are_counted_and_dated():
    today = date(2026, 9, 2)
    contracts = [
        _meta("a", "call", 1), _meta("b", "put", 1),
        _meta("c", "call", 1, expiry=date(2026, 9, 4)),
        _meta("d", "call", 1, expiry=date(2026, 8, 28)),  # already expired: dropped
    ]
    out = expiries_from_contracts(contracts, today)
    assert [(e.expiry, e.dte, e.contract_count) for e in out] == [
        (date(2026, 9, 4), 2, 1),
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
    contracts = asyncio.run(fetch_contracts(clients, "SPY", date(2026, 9, 2), date(2026, 11, 1), 675.0, 825.0))
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
