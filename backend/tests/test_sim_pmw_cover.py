"""The poor man's cover in the simulated book: a covered call written against
a longer-dated long call at or below its strike instead of shares, and the
long call itself rolled out as one package -- SimOptionsService over a real
sqlite file, the way test_sim_roll does it."""

import asyncio
from datetime import date, datetime, timezone

import pytest

from app.core.config import Settings
from app.options.chain import Chain, ExpiryInfo, LegQuote, StrikeRow
from app.options.models import CloseLeg, CloseSpreadRequest, RollRequest, SpreadTicket, TicketLeg
from app.options.occ import format_occ
from app.services.market_clock import ET
from app.trading.errors import OrderRejected
from app.trading.sim.options_book import BookLeg, SimOptionsBook
from app.trading.sim.options_service import SimOptionsService
from app.trading.sim.options_store import SimOptionsStore
from app.trading.sim.store import SimStore

TODAY = date(2026, 8, 12)
NEAR = date(2026, 9, 18)
FAR = date(2027, 6, 18)
FARTHER = date(2027, 12, 17)
NOW = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)
SPOT = 100.0
CALL_105_NEAR = format_occ("XYZ", NEAR, "call", 105.0)
CALL_110_NEAR = format_occ("XYZ", NEAR, "call", 110.0)
CALL_90_FAR = format_occ("XYZ", FAR, "call", 90.0)
CALL_110_FAR = format_occ("XYZ", FAR, "call", 110.0)
CALL_85_FARTHER = format_occ("XYZ", FARTHER, "call", 85.0)


def _quote(symbol: str, bid: float, ask: float, kind: str, strike: float, expiry: date, delta: float) -> LegQuote:
    return LegQuote(
        symbol=symbol, strike=strike, kind=kind, expiry=expiry, bid=bid, ask=ask, mid=round((bid + ask) / 2, 4),
        last=bid, bid_size=5, ask_size=5, delta=delta, gamma=None, theta=None, iv=0.3, open_interest=100, tradable=True,
    )


QUOTES = {
    CALL_105_NEAR: _quote(CALL_105_NEAR, 1.00, 1.10, "call", 105.0, NEAR, 0.3),
    CALL_110_NEAR: _quote(CALL_110_NEAR, 0.40, 0.50, "call", 110.0, NEAR, 0.15),
    CALL_90_FAR: _quote(CALL_90_FAR, 12.00, 12.10, "call", 90.0, FAR, 0.8),
    CALL_110_FAR: _quote(CALL_110_FAR, 3.00, 3.10, "call", 110.0, FAR, 0.35),
    CALL_85_FARTHER: _quote(CALL_85_FARTHER, 16.00, 16.10, "call", 85.0, FARTHER, 0.82),
}


def _chain(expiry: date) -> Chain:
    rows = []
    for strike in (85.0, 90.0, 95.0, 100.0, 105.0, 110.0):
        put_symbol = format_occ("XYZ", expiry, "put", strike)
        call_symbol = format_occ("XYZ", expiry, "call", strike)
        call = QUOTES.get(call_symbol) or _quote(call_symbol, 1.00, 1.10, "call", strike, expiry, 0.3)
        put = _quote(put_symbol, 1.00, 1.10, "put", strike, expiry, -0.3)
        rows.append(StrikeRow(strike=strike, call=call, put=put))
    return Chain(underlying="XYZ", expiry=expiry, spot=SPOT, feed="sim", as_of=NOW, rows=rows)


class _Source:
    feed = "sim"

    def __init__(self):
        self.quotes = QUOTES
        self.as_of = None

    def now(self):
        return NOW.astimezone(ET)

    async def spot(self, underlying):
        return SPOT

    async def expiries(self, underlying):
        return SPOT, [ExpiryInfo(expiry=e, dte=(e - TODAY).days, contract_count=12) for e in (NEAR, FAR, FARTHER)]

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


def _service(env, cash: float = 10_000.0) -> SimOptionsService:
    sim, options = env
    return SimOptionsService(
        None,  # type: ignore[arg-type]
        Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s"),
        sim_store=sim,
        options_store=options,
        user_id=3,
        source=_Source(),
        sim_service=_SimService(cash),  # type: ignore[arg-type]
    )


def _open_long_call(env, symbol: str, strike: float, expiry: date) -> None:
    sim, options = env
    book = SimOptionsBook(options, sim, 3)
    asyncio.run(
        book.submit(
            legs=[BookLeg(symbol=symbol, kind="call", strike=strike, expiry=expiry, side="buy", position_intent="buy_to_open")],
            qty=1, direction="debit", limit_price=None, underlying="XYZ", strategy="long_call",
            client_order_id=None, quotes=QUOTES, now=NOW, source="sim",
        )
    )


def _covered_call(strike: float = 105.0) -> SpreadTicket:
    return SpreadTicket(underlying="XYZ", strategy="covered_call", expiry=NEAR, qty=1, legs=[TicketLeg(kind="call", strike=strike, side="sell")])


def test_a_longer_dated_call_at_or_below_the_strike_covers_a_short_call(env):
    sim, options = env
    _open_long_call(env, CALL_90_FAR, 90.0, FAR)
    service = _service(env)

    preview = asyncio.run(service.preview(_covered_call()))
    assert preview.coverage is not None
    assert preview.coverage.kind == "cover" and preview.coverage.have == 100 and preview.coverage.need == 100 and preview.coverage.ok
    assert not any("needs" in w for w in preview.warnings)

    order = asyncio.run(service.submit(_covered_call()))
    assert order["status"] == "filled"
    positions = {p["symbol"]: p for p in asyncio.run(options.list_positions(3))}
    assert positions[CALL_105_NEAR]["side"] == "short" and positions[CALL_90_FAR]["side"] == "long"
    # Cash: -12.10 for the long call, +1.00 for the short.
    assert round(asyncio.run(sim.get_account_row(3))["cash"], 2) == round(10_000.0 - 1_210.0 + 100.0, 2)


def test_no_cover_and_a_higher_or_shorter_long_call_do_not_count(env):
    service = _service(env)
    bare = asyncio.run(service.preview(_covered_call()))
    assert bare.coverage is not None and bare.coverage.kind == "shares" and not bare.coverage.ok
    assert any("longer-dated call" in w for w in bare.warnings)
    with pytest.raises(OrderRejected):
        asyncio.run(service.submit(_covered_call()))

    # A long call above the short strike covers nothing.
    _open_long_call(env, CALL_110_FAR, 110.0, FAR)
    above = asyncio.run(service.preview(_covered_call(105.0)))
    assert not above.coverage.ok
    # At or below it, it does -- the 110 short call is covered by the 110 long.
    at = asyncio.run(service.preview(_covered_call(110.0)))
    assert at.coverage.ok and at.coverage.kind == "cover"


def test_a_short_call_expiring_after_the_long_call_is_not_covered(env):
    _open_long_call(env, CALL_105_NEAR, 105.0, NEAR)
    service = _service(env)
    later = SpreadTicket(underlying="XYZ", strategy="covered_call", expiry=FAR, qty=1, legs=[TicketLeg(kind="call", strike=110.0, side="sell")])
    preview = asyncio.run(service.preview(later))
    assert not preview.coverage.ok


def test_the_long_call_is_rolled_out_as_one_package(env):
    sim, options = env
    _open_long_call(env, CALL_90_FAR, 90.0, FAR)
    service = _service(env)
    roll = RollRequest(
        close=CloseSpreadRequest(legs=[CloseLeg(symbol=CALL_90_FAR, qty=1)], qty=1),
        open=SpreadTicket(underlying="XYZ", strategy="long_call", expiry=FARTHER, qty=1, long_strike=85.0),
    )
    preview = asyncio.run(service.preview_roll(roll))
    # Sold at the bid 12.00, bought at the ask 16.10: a debit of 4.10 at the natural.
    assert preview["net"]["direction"] == "debit" and preview["net"]["natural"] == pytest.approx(4.10)
    assert preview["can_submit"] is True

    result = asyncio.run(service.roll(roll))
    assert result["order"]["status"] == "filled"
    positions = {p["symbol"]: p for p in asyncio.run(options.list_positions(3))}
    assert CALL_90_FAR not in positions and positions[CALL_85_FARTHER]["side"] == "long"
    assert round(asyncio.run(sim.get_account_row(3))["cash"], 2) == round(10_000.0 - 1_210.0 + 1_200.0 - 1_610.0, 2)
    orders = asyncio.run(options.list_orders(3, status="all"))
    rolled = next(o for o in orders if o["strategy"] == "roll")
    intents = sorted(l["position_intent"] for l in rolled["legs"])
    assert intents == ["buy_to_open", "sell_to_close"]


def test_a_roll_may_not_open_a_spread():
    with pytest.raises(ValueError):
        RollRequest(
            close=CloseSpreadRequest(legs=[CloseLeg(symbol=CALL_90_FAR, qty=1)], qty=1),
            open=SpreadTicket(underlying="XYZ", strategy="bull_call", expiry=FARTHER, qty=1, long_strike=85.0, short_strike=95.0),
        )
