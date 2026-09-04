"""SimOptionsService: positions classified like Alpaca's, collateral,
the Order shape, and the book's exit triggers over a real TriggerStore."""

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pytest

from app.core.config import Settings
from app.options.chain import LegQuote
from app.options.models import TriggerCreate
from app.options.occ import format_occ
from app.options.positions import SpreadGroup, SpreadPositionLeg
from app.options.sim_monitor import check_sim_triggers
from app.options.trigger_store import TriggerStore
from app.services.market_clock import ET
from app.trading.sim.options_book import BookLeg, SimOptionsBook
from app.trading.sim.options_service import SimOptionsService, collateral_for, public_option_order
from app.trading.sim.options_store import SimOptionsStore
from app.trading.sim.store import SimStore

REPLAY_DAY = date(2026, 8, 12)
EXPIRY = date(2026, 8, 14)
PUT_745 = format_occ("SPY", EXPIRY, "put", 745.0)
PUT_740 = format_occ("SPY", EXPIRY, "put", 740.0)
CALL_750 = format_occ("SPY", EXPIRY, "call", 750.0)
NOW = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)


def _quote(symbol: str, bid: float, ask: float, kind: str, strike: float) -> LegQuote:
    return LegQuote(
        symbol=symbol, strike=strike, kind=kind, expiry=EXPIRY, bid=bid, ask=ask, mid=round((bid + ask) / 2, 4),
        last=bid, bid_size=None, ask_size=None, delta=None, gamma=None, theta=None, iv=None, open_interest=0, tradable=True,
    )


class _Source:
    feed = "replay"

    def __init__(self, quotes: dict[str, LegQuote], spot: float = 748.0):
        self.quotes = quotes
        self.spot_price = spot
        self.as_of = NOW

    def now(self):
        return NOW.astimezone(ET)

    async def spot(self, underlying):
        return self.spot_price

    async def expiries(self, underlying):
        raise LookupError("not in this test")

    async def chain(self, underlying, expiry):
        raise LookupError("not in this test")

    async def leg_quotes(self, symbols):
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


class _SimService:
    async def account(self):
        return {"equity": "25000.00", "buying_power": "25000.00", "cash": "25000.00"}


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "sim.sqlite3")
    sim = SimStore(db)
    options = SimOptionsStore(db)
    triggers = TriggerStore(db)
    asyncio.run(sim.init_schema())
    asyncio.run(options.init_schema())
    asyncio.run(triggers.init_schema())
    asyncio.run(sim.ensure_account(3, 25_000.0))
    return sim, options, triggers


def _settings() -> Settings:
    return Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s")


def _service(env, quotes: dict[str, LegQuote], spot: float = 748.0) -> SimOptionsService:
    sim, options, _triggers = env
    return SimOptionsService(
        None,  # type: ignore[arg-type]
        _settings(),
        sim_store=sim,
        options_store=options,
        user_id=3,
        source=_Source(quotes, spot),
        sim_service=_SimService(),  # type: ignore[arg-type]
    )


def _open_bull_put(env, qty: int = 2) -> None:
    sim, options, _ = env
    book = SimOptionsBook(options, sim, 3)
    quotes = {PUT_745: _quote(PUT_745, 3.00, 3.10, "put", 745.0), PUT_740: _quote(PUT_740, 2.10, 2.20, "put", 740.0)}
    asyncio.run(
        book.submit(
            legs=[
                BookLeg(symbol=PUT_745, kind="put", strike=745.0, expiry=EXPIRY, side="sell", position_intent="sell_to_open"),
                BookLeg(symbol=PUT_740, kind="put", strike=740.0, expiry=EXPIRY, side="buy", position_intent="buy_to_open"),
            ],
            qty=qty, direction="credit", limit_price=None, underlying="SPY", strategy="bull_put",
            client_order_id=None, quotes=quotes, now=NOW, source="replay",
        )
    )


def test_positions_group_into_a_bull_put_with_dte_from_the_replay_clock(env):
    _open_bull_put(env)
    quotes = {PUT_745: _quote(PUT_745, 2.50, 2.60, "put", 745.0), PUT_740: _quote(PUT_740, 1.80, 1.90, "put", 740.0)}
    service = _service(env, quotes)
    marks = asyncio.run(service.marked_positions())
    by_symbol = {m["symbol"]: m for m in marks}
    assert by_symbol[PUT_745]["qty"] == "-2" and by_symbol[PUT_740]["qty"] == "2"
    assert by_symbol[PUT_745]["asset_class"] == "us_option"
    # Short at 3.00, marked at the 2.55 mid: +0.45 x 100 x 2.
    assert by_symbol[PUT_745]["unrealized_pl"] == "90.00"
    groups = asyncio.run(service.spreads())
    assert len(groups) == 1
    group = groups[0]
    assert group.strategy == "bull_put" and group.qty == 2 and group.account == "sim"
    assert group.dte == (EXPIRY - REPLAY_DAY).days
    assert group.net_entry == round(-3.00 + 2.20, 4)
    account = asyncio.run(service.account())
    assert account["account"] == "sim" and account["feed"] == "replay"
    assert account["options_trading_level"] == 3
    # Collateral: (5 - 0.80) x 100 x 2 held back from buying power.
    assert account["reserved_collateral"] == 840.0
    assert account["options_buying_power"] == 25_000.0 - 840.0
    assert account["replay_as_of"] == NOW.isoformat()


def test_collateral_for_the_common_shapes():
    def leg(symbol, kind, strike, qty):
        return SpreadPositionLeg(symbol=symbol, kind=kind, strike=strike, qty=qty, avg_entry_price=1.0, current_price=1.0,
                                 market_value=0.0, unrealized_pl=0.0, cost_basis=0.0, expiry=EXPIRY)

    def group(strategy, legs, net_entry, qty=1):
        return SpreadGroup(id="g", underlying="SPY", root="SPY", expiry=EXPIRY, dte=2, strategy=strategy, qty=qty,
                           legs=legs, net_entry=net_entry)

    assert collateral_for(group("bull_put", [leg(PUT_740, "put", 740.0, 1), leg(PUT_745, "put", 745.0, -1)], -0.8)) == 420.0
    assert collateral_for(group("long_call", [leg(CALL_750, "call", 750.0, 1)], 2.5)) == 0.0
    assert collateral_for(group("cash_secured_put", [leg(PUT_740, "put", 740.0, -1)], -2.0)) == 74_000.0
    assert collateral_for(group("covered_call", [leg(CALL_750, "call", 750.0, -1)], -1.0)) == 0.0
    condor = [leg(PUT_740, "put", 740.0, 1), leg(PUT_745, "put", 745.0, -1),
              leg(CALL_750, "call", 750.0, -1), leg(format_occ("SPY", EXPIRY, "call", 760.0), "call", 760.0, 1)]
    assert collateral_for(group("iron_condor", condor, -1.5)) == (10 - 1.5) * 100


def test_public_order_shape_nests_the_legs_of_a_package(env):
    _open_bull_put(env)
    _sim, options, _ = env
    rows = asyncio.run(options.list_orders(3, "all"))
    order = public_option_order(rows[0])
    assert order["symbol"] == "SPY" and order["order_class"] == "mleg" and order["asset_class"] == "us_option"
    assert order["side"] == "sell" and order["status"] == "filled" and order["qty"] == "2"
    assert order["filled_avg_price"] == "0.80"
    assert [l["symbol"] for l in order["legs"]] == [PUT_745, PUT_740]
    assert order["legs"][0]["filled_avg_price"] == "3.00"
    single = public_option_order({**rows[0], "legs": rows[0]["legs"][:1], "direction": "debit"})
    assert single["symbol"] == PUT_745 and single["order_class"] == "simple" and single["legs"] is None


def test_close_contract_reverses_what_is_held(env):
    _open_bull_put(env, qty=1)
    quotes = {PUT_745: _quote(PUT_745, 2.50, 2.60, "put", 745.0), PUT_740: _quote(PUT_740, 1.80, 1.90, "put", 740.0)}
    service = _service(env, quotes)
    order = asyncio.run(service.close_contract(PUT_745))
    assert order["status"] == "filled" and order["side"] == "buy" and order["symbol"] == PUT_745
    _sim, options, _ = env
    assert asyncio.run(options.get_position(3, PUT_745)) is None
    assert asyncio.run(options.get_position(3, PUT_740)) is not None


@dataclass
class _FakeService:
    """What check_sim_triggers needs of the service: a spot, quotes, the
    held groups and a close that records its call."""

    spot_price: float
    held: dict
    quotes: dict = field(default_factory=dict)
    closes: list = field(default_factory=list)

    @property
    def source(self):
        service = self

        class _S:
            async def leg_quotes(self, symbols):
                return {s: service.quotes[s] for s in symbols if s in service.quotes}

        return _S()

    async def spot(self, underlying):
        return self.spot_price

    async def spreads(self):
        @dataclass
        class _Leg:
            symbol: str
            qty: int

        @dataclass
        class _Group:
            legs: list

        return [_Group([_Leg(s, q) for s, q in self.held.items()])]

    async def close_spread(self, req, confirm=None, *, marketable=False):
        self.closes.append((req, marketable))
        return {"id": "sim-close-1"}


def test_sim_triggers_fire_against_the_books_prices_without_the_trading_gate(env):
    _sim, _options, triggers = env
    body = TriggerCreate(
        underlying="SPY", expiry=EXPIRY, legs=[{"symbol": PUT_745, "qty": -1}, {"symbol": PUT_740, "qty": 1}], qty=1,
        close_below=745.0,
    )
    armed = asyncio.run(triggers.create(3, "sim", body))
    # A paper trigger of the same user is not this loop's business.
    asyncio.run(triggers.create(3, "paper", body))
    # Trading is switched off in these settings: the sim book does not care.
    settings = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s", trading_enabled=False)
    service = _FakeService(spot_price=746.0, held={PUT_745: -1, PUT_740: 1})
    asyncio.run(check_sim_triggers(3, service, triggers, settings))
    assert service.closes == []
    service.spot_price = 744.5
    asyncio.run(check_sim_triggers(3, service, triggers, settings))
    assert len(service.closes) == 1
    req, marketable = service.closes[0]
    assert marketable is True and req.qty == 1 and {l.symbol for l in req.legs} == {PUT_745, PUT_740}
    rows = {t["account"]: t for t in asyncio.run(triggers.list_for_user(3, "sim")) + asyncio.run(triggers.list_for_user(3, "paper"))}
    assert rows["sim"]["status"] == "fired" and rows["sim"]["fired_order_id"] == "sim-close-1"
    assert rows["sim"]["id"] == armed["id"]
    assert rows["paper"]["status"] == "active"
    assert [t["account"] for t in asyncio.run(triggers.all_active())] == ["paper"]
    assert asyncio.run(triggers.all_active("sim")) == []


def test_premium_trigger_uses_the_books_quotes(env):
    _sim, _options, triggers = env
    body = TriggerCreate(underlying="SPY", expiry=EXPIRY, legs=[{"symbol": CALL_750, "qty": 1}], qty=1, premium_below=1.50)
    asyncio.run(triggers.create(3, "sim", body))
    settings = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s")
    service = _FakeService(spot_price=748.0, held={CALL_750: 1}, quotes={CALL_750: _quote(CALL_750, 1.60, 1.70, "call", 750.0)})
    asyncio.run(check_sim_triggers(3, service, triggers, settings))
    assert service.closes == []
    service.quotes = {CALL_750: _quote(CALL_750, 1.40, 1.50, "call", 750.0)}
    asyncio.run(check_sim_triggers(3, service, triggers, settings))
    assert len(service.closes) == 1
    fired = asyncio.run(triggers.list_for_user(3, "sim"))[0]
    assert fired["status"] == "fired" and fired["fired_on"] == "premium" and fired["fired_price"] == 1.45
