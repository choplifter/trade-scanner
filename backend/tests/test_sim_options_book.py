"""The simulated options book's fills, positions, cash, round trips and
expiry settlement -- over a real sqlite file in tmp_path, no client."""

import asyncio
from datetime import date, datetime, time, timezone

import pytest

from app.options.chain import LegQuote
from app.options.occ import format_occ
from app.options.pricing import option_slippage
from app.services.market_clock import ET
from app.trading.errors import OrderRejected
from app.trading.sim.options_book import BookLeg, SimOptionsBook, leg_fill_price, limit_allows, net_fill
from app.trading.sim.options_store import SimOptionsStore
from app.trading.sim.store import SimStore

EXPIRY = date(2026, 8, 14)
CALL_100 = format_occ("XYZ", EXPIRY, "call", 100.0)
PUT_100 = format_occ("XYZ", EXPIRY, "put", 100.0)
PUT_95 = format_occ("XYZ", EXPIRY, "put", 95.0)
NOW = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)


def _quote(symbol: str, bid: float | None, ask: float | None, last: float | None = None, strike: float = 100.0, kind: str = "call") -> LegQuote:
    mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None and bid > 0 else last
    return LegQuote(
        symbol=symbol, strike=strike, kind=kind, expiry=EXPIRY, bid=bid, ask=ask, mid=mid, last=last,
        bid_size=None, ask_size=None, delta=None, gamma=None, theta=None, iv=None, open_interest=0, tradable=True,
    )


class _Source:
    feed = "replay"

    def __init__(self, quotes: dict[str, LegQuote], spot: float | None = 100.0):
        self.quotes = quotes
        self._spot = spot

    def now(self):
        return NOW.astimezone(ET)

    async def spot(self, underlying):
        return self._spot

    async def leg_quotes(self, symbols):
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


@pytest.fixture
def stores(tmp_path):
    db = str(tmp_path / "sim.sqlite3")
    sim = SimStore(db)
    options = SimOptionsStore(db)
    asyncio.run(sim.init_schema())
    asyncio.run(options.init_schema())
    asyncio.run(sim.ensure_account(7, 25_000.0))
    return sim, options


def _cash(sim: SimStore) -> float:
    return asyncio.run(sim.get_account_row(7))["cash"]


def _leg(symbol: str, side: str, kind: str = "call", strike: float = 100.0, ratio: int = 1) -> BookLeg:
    return BookLeg(symbol=symbol, kind=kind, strike=strike, expiry=EXPIRY, side=side, ratio_qty=ratio)


def test_leg_fill_price_takes_the_natural_side_or_mid_plus_slippage():
    two_sided = _quote(CALL_100, 2.00, 2.20)
    assert leg_fill_price(two_sided, "buy") == 2.20
    assert leg_fill_price(two_sided, "sell") == 2.00
    one_sided = _quote(CALL_100, None, 2.20, last=2.05)
    assert leg_fill_price(one_sided, "buy") == 2.20
    # No bid: the last less its slippage.
    assert leg_fill_price(one_sided, "sell") == round(2.05 - option_slippage(2.05), 4)
    assert leg_fill_price(None, "buy") is None
    assert leg_fill_price(_quote(CALL_100, None, None, last=None), "buy") is None


def test_net_fill_and_limits():
    quotes = {PUT_100: _quote(PUT_100, 2.00, 2.10, kind="put"), PUT_95: _quote(PUT_95, 1.10, 1.20, kind="put", strike=95.0)}
    legs = [_leg(PUT_100, "sell", "put"), _leg(PUT_95, "buy", "put", 95.0)]
    signed, per_leg = net_fill(legs, quotes)
    assert signed == round(-2.00 + 1.20, 4)  # a credit of 0.80
    assert per_leg == {PUT_100: 2.00, PUT_95: 1.20}
    assert limit_allows("credit", None, signed)
    assert limit_allows("credit", 0.80, signed)
    assert limit_allows("credit", 0.75, signed)
    assert not limit_allows("credit", 0.85, signed)
    assert limit_allows("debit", 2.20, 2.20) and not limit_allows("debit", 2.10, 2.20)
    assert net_fill(legs, {PUT_100: quotes[PUT_100]}) == (None, {})


def test_market_buy_fills_at_the_ask_and_debits_cash(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    quotes = {CALL_100: _quote(CALL_100, 2.00, 2.10)}
    order = asyncio.run(
        book.submit(
            legs=[_leg(CALL_100, "buy")], qty=2, direction="debit", limit_price=None, underlying="XYZ",
            strategy="long_call", client_order_id="c1", quotes=quotes, now=NOW, source="sim",
        )
    )
    assert order["status"] == "filled" and order["filled_qty"] == 2 and order["net_fill_price"] == 2.10
    assert order["legs"][0]["fill_price"] == 2.10
    position = asyncio.run(options.get_position(7, CALL_100))
    assert position["side"] == "long" and position["qty"] == 2 and position["avg_entry_price"] == 2.10
    assert position["opened_at"] == NOW.isoformat()
    assert _cash(sim) == 25_000.0 - 2 * 2.10 * 100


def test_a_limit_below_the_ask_rests_and_fills_when_the_market_comes_down(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    quotes = {CALL_100: _quote(CALL_100, 2.00, 2.10)}
    order = asyncio.run(
        book.submit(
            legs=[_leg(CALL_100, "buy")], qty=1, direction="debit", limit_price=2.00, underlying="XYZ",
            strategy="long_call", client_order_id=None, quotes=quotes, now=NOW, source="replay",
        )
    )
    assert order["status"] == "new" and _cash(sim) == 25_000.0
    assert asyncio.run(book.check_fills(_Source({CALL_100: _quote(CALL_100, 1.95, 2.05)}), NOW)) == []
    filled = asyncio.run(book.check_fills(_Source({CALL_100: _quote(CALL_100, 1.90, 1.95)}), NOW))
    assert len(filled) == 1 and filled[0]["net_fill_price"] == 1.95
    assert _cash(sim) == 25_000.0 - 195.0
    assert asyncio.run(options.working_orders(7)) == []


def test_cancel_only_touches_a_resting_package(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    order = asyncio.run(
        book.submit(
            legs=[_leg(CALL_100, "buy")], qty=1, direction="debit", limit_price=1.00, underlying="XYZ",
            strategy="long_call", client_order_id=None, quotes={CALL_100: _quote(CALL_100, 2.00, 2.10)}, now=NOW, source="sim",
        )
    )
    cancelled = asyncio.run(book.cancel(order["id"], NOW))
    assert cancelled["status"] == "canceled" and cancelled["canceled_at"] == NOW.isoformat()
    with pytest.raises(OrderRejected):
        asyncio.run(book.cancel(order["id"], NOW))


def test_closing_writes_a_round_trip_with_the_contract_multiplier(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    asyncio.run(
        book.submit(
            legs=[_leg(CALL_100, "buy")], qty=1, direction="debit", limit_price=None, underlying="XYZ",
            strategy="long_call", client_order_id=None, quotes={CALL_100: _quote(CALL_100, 2.00, 2.10)}, now=NOW, source="sim",
        )
    )
    later = datetime(2026, 8, 12, 15, 30, tzinfo=timezone.utc)
    order = asyncio.run(
        book.submit(
            legs=[BookLeg(symbol=CALL_100, kind="call", strike=100.0, expiry=EXPIRY, side="sell", position_intent="sell_to_close")],
            qty=1, direction="credit", limit_price=None, underlying="XYZ", strategy="close", client_order_id=None,
            quotes={CALL_100: _quote(CALL_100, 3.00, 3.10)}, now=later, source="sim",
        )
    )
    assert order["status"] == "filled" and order["net_fill_price"] == -3.00
    assert asyncio.run(options.get_position(7, CALL_100)) is None
    trades = asyncio.run(sim.list_trades(7))
    assert len(trades) == 1
    trade = trades[0]
    assert trade["symbol"] == CALL_100 and trade["qty"] == 1
    assert trade["entry_avg"] == 2.10 and trade["exit_avg"] == 3.00
    assert trade["pnl"] == 90.0 and trade["multiplier"] == 100
    assert trade["opened_at"] == NOW.isoformat() and trade["closed_at"] == later.isoformat()
    assert _cash(sim) == 25_000.0 - 210.0 + 300.0


def test_a_credit_spread_credits_cash_and_opens_both_legs(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    quotes = {PUT_100: _quote(PUT_100, 2.00, 2.10, kind="put"), PUT_95: _quote(PUT_95, 1.10, 1.20, kind="put", strike=95.0)}
    order = asyncio.run(
        book.submit(
            legs=[_leg(PUT_100, "sell", "put"), _leg(PUT_95, "buy", "put", 95.0)], qty=3, direction="credit",
            limit_price=0.80, underlying="XYZ", strategy="bull_put", client_order_id=None, quotes=quotes, now=NOW, source="sim",
        )
    )
    assert order["status"] == "filled" and order["net_fill_price"] == -0.8
    short = asyncio.run(options.get_position(7, PUT_100))
    long = asyncio.run(options.get_position(7, PUT_95))
    assert short["side"] == "short" and short["qty"] == 3
    assert long["side"] == "long" and long["qty"] == 3
    assert round(_cash(sim), 2) == round(25_000.0 + 3 * 0.80 * 100, 2)


def test_expiry_settles_at_intrinsic_against_the_spot(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    quotes = {CALL_100: _quote(CALL_100, 2.00, 2.10), PUT_100: _quote(PUT_100, 1.50, 1.60, kind="put")}
    for symbol, kind in ((CALL_100, "call"), (PUT_100, "put")):
        asyncio.run(
            book.submit(
                legs=[_leg(symbol, "buy", kind)], qty=1, direction="debit", limit_price=None, underlying="XYZ",
                strategy=f"long_{kind}", client_order_id=None, quotes=quotes, now=NOW, source="sim",
            )
        )
    before_close = datetime.combine(EXPIRY, time(15, 59), tzinfo=ET)
    assert asyncio.run(book.settle_expired(_Source({}, spot=105.0), before_close)) == []
    at_close = datetime.combine(EXPIRY, time(16, 0), tzinfo=ET)
    settled = asyncio.run(book.settle_expired(_Source({}, spot=105.0), at_close))
    assert {o["legs"][0]["symbol"] for o in settled} == {CALL_100, PUT_100}
    assert all(o["status"] == "expired" for o in settled)
    assert asyncio.run(options.list_positions(7)) == []
    trades = {t["symbol"]: t for t in asyncio.run(sim.list_trades(7))}
    assert trades[CALL_100]["exit_avg"] == 5.0 and trades[CALL_100]["pnl"] == round((5.0 - 2.10) * 100, 2)
    assert trades[PUT_100]["exit_avg"] == 0.0 and trades[PUT_100]["pnl"] == round(-1.60 * 100, 2)
    assert round(_cash(sim), 2) == round(25_000.0 - 210.0 - 160.0 + 500.0, 2)


def test_selling_more_than_held_flips_through_flat(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    asyncio.run(
        book.submit(
            legs=[_leg(CALL_100, "buy")], qty=1, direction="debit", limit_price=None, underlying="XYZ",
            strategy="long_call", client_order_id=None, quotes={CALL_100: _quote(CALL_100, 2.00, 2.10)}, now=NOW, source="sim",
        )
    )
    asyncio.run(
        book.submit(
            legs=[_leg(CALL_100, "sell")], qty=2, direction="credit", limit_price=None, underlying="XYZ",
            strategy="covered_call", client_order_id=None, quotes={CALL_100: _quote(CALL_100, 2.50, 2.60)}, now=NOW, source="sim",
        )
    )
    position = asyncio.run(options.get_position(7, CALL_100))
    assert position["side"] == "short" and position["qty"] == 1 and position["avg_entry_price"] == 2.50
    assert len(asyncio.run(sim.list_trades(7))) == 1
