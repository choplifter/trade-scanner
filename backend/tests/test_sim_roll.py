"""Rolling a short leg in the simulated book -- SimOptionsService.roll and
preview_roll over a real sqlite file, and the pure roll_net arithmetic. The
point under test: a roll is one package, so both legs fill or neither, and
the new leg is judged against the collateral the old one gives back."""

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pytest

from app.core.config import Settings
from app.options.chain import Chain, ExpiryInfo, LegQuote, StrikeRow
from app.options.models import CloseLeg, CloseSpreadRequest, ResolvedSpread, RollRequest, SpreadTicket, TicketLeg
from app.options.occ import format_occ
from app.options.service import released_collateral, roll_net
from app.options.models import closing_legs
from app.services.market_clock import ET
from app.trading.errors import OrderRejected
from app.trading.sim.options_book import BookLeg, SimOptionsBook
from app.trading.sim.options_service import SimOptionsService
from app.trading.sim.options_store import SimOptionsStore
from app.trading.sim.store import SimStore

TODAY = date(2026, 8, 12)
NEAR = date(2026, 8, 14)
FAR = date(2026, 9, 18)
NOW = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)
SPOT = 100.0
PUT_95_NEAR = format_occ("XYZ", NEAR, "put", 95.0)
PUT_95_FAR = format_occ("XYZ", FAR, "put", 95.0)
PUT_90_FAR = format_occ("XYZ", FAR, "put", 90.0)


def _quote(symbol: str, bid: float, ask: float, kind: str, strike: float, expiry: date) -> LegQuote:
    return LegQuote(
        symbol=symbol, strike=strike, kind=kind, expiry=expiry, bid=bid, ask=ask, mid=round((bid + ask) / 2, 4),
        last=bid, bid_size=5, ask_size=5, delta=-0.3 if kind == "put" else 0.3, gamma=None, theta=None, iv=0.3,
        open_interest=100, tradable=True,
    )


QUOTES = {
    PUT_95_NEAR: _quote(PUT_95_NEAR, 0.40, 0.50, "put", 95.0, NEAR),
    PUT_95_FAR: _quote(PUT_95_FAR, 2.00, 2.10, "put", 95.0, FAR),
    PUT_90_FAR: _quote(PUT_90_FAR, 1.00, 1.10, "put", 90.0, FAR),
}


def _chain(expiry: date) -> Chain:
    rows = []
    for strike in (90.0, 95.0, 100.0, 105.0):
        put_symbol = format_occ("XYZ", expiry, "put", strike)
        call_symbol = format_occ("XYZ", expiry, "call", strike)
        put = QUOTES.get(put_symbol) or _quote(put_symbol, 1.00, 1.10, "put", strike, expiry)
        call = _quote(call_symbol, 1.00, 1.10, "call", strike, expiry)
        rows.append(StrikeRow(strike=strike, call=call, put=put))
    return Chain(underlying="XYZ", expiry=expiry, spot=SPOT, feed="sim", as_of=NOW, rows=rows)


class _Source:
    feed = "sim"

    def __init__(self, quotes=None):
        self.quotes = quotes or QUOTES
        self.as_of = None

    def now(self):
        return NOW.astimezone(ET)

    async def spot(self, underlying):
        return SPOT

    async def expiries(self, underlying):
        return SPOT, [ExpiryInfo(expiry=e, dte=(e - TODAY).days, contract_count=8) for e in (NEAR, FAR)]

    async def chain(self, underlying, expiry):
        return _chain(expiry)

    async def leg_quotes(self, symbols):
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


class _SimService:
    def __init__(self, cash: float):
        self.cash = cash

    async def account(self):
        return {"equity": f"{self.cash:.2f}", "buying_power": f"{self.cash:.2f}", "cash": f"{self.cash:.2f}"}


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "sim.sqlite3")
    sim = SimStore(db)
    options = SimOptionsStore(db)
    asyncio.run(sim.init_schema())
    asyncio.run(options.init_schema())
    asyncio.run(sim.ensure_account(3, 10_000.0))
    return sim, options


def _service(env, cash: float = 10_000.0, quotes=None) -> SimOptionsService:
    sim, options = env
    return SimOptionsService(
        None,  # type: ignore[arg-type]
        Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s"),
        sim_store=sim,
        options_store=options,
        user_id=3,
        source=_Source(quotes),
        sim_service=_SimService(cash),  # type: ignore[arg-type]
    )


def _open_short_put(env) -> None:
    sim, options = env
    book = SimOptionsBook(options, sim, 3)
    asyncio.run(
        book.submit(
            legs=[BookLeg(symbol=PUT_95_NEAR, kind="put", strike=95.0, expiry=NEAR, side="sell", position_intent="sell_to_open")],
            qty=1, direction="credit", limit_price=None, underlying="XYZ", strategy="cash_secured_put",
            client_order_id=None, quotes=QUOTES, now=NOW, source="sim",
        )
    )


def _roll(strike: float = 95.0, limit_net=None, limit_direction=None) -> RollRequest:
    return RollRequest(
        close=CloseSpreadRequest(legs=[CloseLeg(symbol=PUT_95_NEAR, qty=-1)], qty=1),
        open=SpreadTicket(underlying="XYZ", strategy="cash_secured_put", expiry=FAR, qty=1, legs=[TicketLeg(kind="put", strike=strike, side="sell")]),
        limit_net=limit_net,
        limit_direction=limit_direction,
    )


# --- pure arithmetic ---------------------------------------------------------------


def test_released_collateral_is_the_strike_of_a_lone_short_put_being_closed():
    legs = closing_legs([CloseLeg(symbol=PUT_95_NEAR, qty=-2)])
    assert released_collateral(legs, 2) == 95.0 * 100 * 2
    # A long put being sold, or a short call, frees no cash collateral.
    assert released_collateral(closing_legs([CloseLeg(symbol=PUT_95_NEAR, qty=2)]), 2) == 0.0
    assert released_collateral(closing_legs([CloseLeg(symbol=format_occ("XYZ", NEAR, "call", 105.0), qty=-1)]), 1) == 0.0


def _resolved(direction: str, mid: float, natural: float | None) -> ResolvedSpread:
    return ResolvedSpread(
        underlying="XYZ", strategy="cash_secured_put", expiry=FAR, qty=1, direction=direction, legs=[], spot=SPOT, width=0.0,
        net_mid=mid, net_natural=natural, limit_price=mid, alpaca_limit_price=mid if direction == "debit" else -mid,
        max_profit=None, max_loss=None, breakevens=[], collateral=0.0, options_buying_power=0.0, dte=30, options_level=3, account="sim",
    )


def test_roll_net_adds_the_close_cost_and_the_open_credit():
    close = {"direction": "debit", "net_mid": 0.45, "net_natural": 0.50}
    net = roll_net(close, _resolved("credit", 2.05, 2.00))
    assert net["direction"] == "credit" and net["mid"] == 1.6 and net["natural"] == 1.5
    # Closing costs more than the new leg brings: a debit roll.
    net = roll_net({"direction": "debit", "net_mid": 2.05, "net_natural": 2.10}, _resolved("credit", 0.45, 0.40))
    assert net["direction"] == "debit" and net["mid"] == 1.6 and net["natural"] == 1.7
    # Mid says credit, natural says debit: no natural is reported.
    net = roll_net({"direction": "debit", "net_mid": 1.00, "net_natural": 1.30}, _resolved("credit", 1.10, 1.00))
    assert net["direction"] == "credit" and net["natural"] is None


# --- the book ----------------------------------------------------------------------


def test_a_roll_fills_both_legs_as_one_package(env):
    sim, options = env
    _open_short_put(env)
    service = _service(env)

    preview = asyncio.run(service.preview_roll(_roll()))
    # Close at the ask 0.50, open at the bid 2.00: a credit of 1.50 at the natural.
    assert preview["net"]["direction"] == "credit"
    assert preview["net"]["natural"] == 1.5 and preview["net"]["suggested_limit"] == 1.5
    assert preview["can_submit"] is True
    assert preview["collateral_delta"] == 0.0  # same strike: nothing new to reserve

    result = asyncio.run(service.roll(_roll()))

    order = result["order"]
    assert order["status"] == "filled" and result["close_order"] is None and result["open_error"] is None
    positions = {p["symbol"]: p for p in asyncio.run(options.list_positions(3))}
    assert PUT_95_NEAR not in positions
    assert positions[PUT_95_FAR]["side"] == "short" and positions[PUT_95_FAR]["qty"] == 1
    # Cash: +0.40 credit at open, -0.50 to close, +2.00 for the new leg.
    assert round(asyncio.run(sim.get_account_row(3))["cash"], 2) == round(10_000.0 + 40.0 - 50.0 + 200.0, 2)
    orders = asyncio.run(options.list_orders(3, status="all"))
    assert any(o["strategy"] == "roll" and len(o["legs"]) == 2 for o in orders)


def test_a_roll_with_a_limit_the_market_does_not_meet_rests_whole(env):
    sim, options = env
    _open_short_put(env)
    service = _service(env)

    result = asyncio.run(service.roll(_roll(limit_net=1.80, limit_direction="credit")))

    assert result["order"]["status"] == "new"
    positions = {p["symbol"]: p for p in asyncio.run(options.list_positions(3))}
    assert set(positions) == {PUT_95_NEAR} and positions[PUT_95_NEAR]["qty"] == 1
    assert round(asyncio.run(sim.get_account_row(3))["cash"], 2) == 10_040.0
    # And a second roll while it rests is refused: the leg is already committed.
    with pytest.raises(OrderRejected):
        asyncio.run(service.roll(_roll()))


def test_the_new_leg_is_judged_with_the_old_collateral_released(env):
    _open_short_put(env)
    # 9,540 of cash: less than the 9,500 the new put needs plus what the old
    # one still reserves, but enough once the old one is closed.
    service = _service(env, cash=9_540.0)

    preview = asyncio.run(service.preview_roll(_roll()))
    assert preview["can_submit"] is True

    # Rolling down to 90 frees 500 of collateral.
    down = asyncio.run(service.preview_roll(_roll(strike=90.0)))
    assert down["collateral_delta"] == -500.0


def test_a_roll_must_share_the_underlying_and_the_count():
    with pytest.raises(ValueError):
        RollRequest(
            close=CloseSpreadRequest(legs=[CloseLeg(symbol=PUT_95_NEAR, qty=-1)], qty=1),
            open=SpreadTicket(underlying="ABC", strategy="cash_secured_put", expiry=FAR, qty=1, legs=[TicketLeg(kind="put", strike=95.0, side="sell")]),
        )
    with pytest.raises(ValueError):
        RollRequest(
            close=CloseSpreadRequest(legs=[CloseLeg(symbol=PUT_95_NEAR, qty=-2)], qty=2),
            open=SpreadTicket(underlying="XYZ", strategy="cash_secured_put", expiry=FAR, qty=1, legs=[TicketLeg(kind="put", strike=95.0, side="sell")]),
        )
    with pytest.raises(ValueError):
        _roll(limit_net=1.0)  # a limit without its direction
