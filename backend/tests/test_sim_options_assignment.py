"""Expiry in the simulated options book once the settlement rules know about
shares: an in-the-money short put becomes 100 shares at the strike, a short
call behind them calls the shares away, and the books (option positions,
stock positions, cash, orders, round trips) all agree. Same sqlite-in-tmp
fixtures as test_sim_options_book."""

import asyncio
from datetime import datetime, time

import pytest

from app.services.market_clock import ET
from app.trading.sim.options_book import SimOptionsBook
from app.trading.sim.options_store import SimOptionsStore
from app.trading.sim.store import SimStore
from tests.test_sim_options_book import CALL_100, EXPIRY, NOW, PUT_100, _Source, _cash, _leg, _quote

AT_CLOSE = datetime.combine(EXPIRY, time(16, 0), tzinfo=ET)
START_CASH = 25_000.0


@pytest.fixture
def stores(tmp_path):
    db = str(tmp_path / "sim.sqlite3")
    sim = SimStore(db)
    options = SimOptionsStore(db)
    asyncio.run(sim.init_schema())
    asyncio.run(options.init_schema())
    asyncio.run(sim.ensure_account(7, START_CASH))
    return sim, options


def _sell(book: SimOptionsBook, symbol: str, kind: str, bid: float, ask: float, qty: int = 1, strategy: str | None = None) -> dict:
    return asyncio.run(
        book.submit(
            legs=[_leg(symbol, "sell", kind)], qty=qty, direction="credit", limit_price=None, underlying="XYZ",
            strategy=strategy or ("cash_secured_put" if kind == "put" else "covered_call"), client_order_id=None,
            quotes={symbol: _quote(symbol, bid, ask, kind=kind)}, now=NOW, source="sim",
        )
    )


def test_an_in_the_money_short_put_is_assigned_into_shares(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    _sell(book, PUT_100, "put", 1.50, 1.60)  # credit 1.50 at the bid
    assert round(_cash(sim), 2) == START_CASH + 150.0

    settled = asyncio.run(book.settle_expired(_Source({}, spot=92.0), AT_CLOSE))

    assert len(settled) == 1
    order = settled[0]
    assert order["status"] == "assigned" and order["strategy"] == "assigned" and order["id"].startswith("assign:")
    assert order["settlement"] == {
        "outcome": "assigned", "share_side": "buy", "share_qty": 100, "share_price": 100.0,
        "share_order_id": f"{order['id']}:shares",
    }
    # The option is gone, its round trip is the premium kept.
    assert asyncio.run(options.list_positions(7)) == []
    trades = asyncio.run(sim.list_trades(7))
    assert len(trades) == 1
    assert trades[0]["symbol"] == PUT_100 and trades[0]["exit_avg"] == 0.0 and trades[0]["pnl"] == 150.0 and trades[0]["multiplier"] == 100
    # The shares sit in the stock book at the strike, paid for in cash.
    shares = asyncio.run(sim.get_position(7, "XYZ"))
    assert shares["side"] == "long" and shares["qty"] == 100 and shares["avg_entry_price"] == 100.0
    assert shares["entry_order_id"] == f"{order['id']}:shares"
    assert round(_cash(sim), 2) == START_CASH + 150.0 - 10_000.0
    stock_order = asyncio.run(sim.get_order(7, f"{order['id']}:shares"))
    assert stock_order["side"] == "buy" and stock_order["filled_avg_price"] == 100.0 and stock_order["status"] == "filled"


def test_a_covered_call_in_the_money_calls_the_shares_away(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    # Assigned first (spot 92 at an earlier expiry is simulated by the put).
    _sell(book, PUT_100, "put", 1.50, 1.60)
    asyncio.run(book.settle_expired(_Source({}, spot=92.0), AT_CLOSE))
    # Then a covered call on the 100 shares, which finishes in the money.
    _sell(book, CALL_100, "call", 2.00, 2.10)
    cash_before = _cash(sim)

    settled = asyncio.run(book.settle_expired(_Source({}, spot=108.0), AT_CLOSE))

    assert [o["strategy"] for o in settled] == ["called_away"]
    assert settled[0]["settlement"]["share_side"] == "sell" and settled[0]["settlement"]["share_qty"] == 100
    assert asyncio.run(sim.get_position(7, "XYZ")) is None
    assert round(_cash(sim), 2) == round(cash_before + 10_000.0, 2)
    trades = {t["symbol"]: t for t in asyncio.run(sim.list_trades(7))}
    # Shares bought at 100 (assignment) and sold at 100 (called away): flat, multiplier 1.
    assert trades["XYZ"]["pnl"] == 0.0 and trades["XYZ"]["multiplier"] == 1
    # The call's round trip is its premium.
    assert trades[CALL_100]["pnl"] == 200.0 and trades[CALL_100]["exit_avg"] == 0.0


def test_an_uncovered_short_call_and_long_options_stay_cash_settled(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    _sell(book, CALL_100, "call", 2.00, 2.10)

    settled = asyncio.run(book.settle_expired(_Source({}, spot=108.0), AT_CLOSE))

    assert [o["strategy"] for o in settled] == ["cash_settled"]
    assert settled[0]["status"] == "expired" and settled[0]["settlement"]["share_qty"] == 0
    assert asyncio.run(sim.get_position(7, "XYZ")) is None
    assert round(_cash(sim), 2) == START_CASH + 200.0 - 800.0


def test_a_put_assigned_today_does_not_cover_a_call_expiring_today(stores):
    sim, options = stores
    book = SimOptionsBook(options, sim, 7)
    _sell(book, PUT_100, "put", 1.50, 1.60)
    _sell(book, CALL_100, "call", 2.00, 2.10)  # sold against nothing

    # Spot 100.5: the put is out of the money (expires), the call is in.
    settled = {o["legs"][0]["symbol"]: o for o in asyncio.run(book.settle_expired(_Source({}, spot=100.5), AT_CLOSE))}

    assert settled[PUT_100]["strategy"] == "expiry"
    assert settled[CALL_100]["strategy"] == "cash_settled"
    assert asyncio.run(sim.get_position(7, "XYZ")) is None
