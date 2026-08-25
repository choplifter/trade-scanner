"""Round trips out of fills: the pairing, its arithmetic, and the record."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.trading.service import OrderService
from app.trading.trade_store import TradeStore
from app.trading.trades import Fill, fills_from_orders, round_trips, summarize

T0 = datetime(2026, 8, 25, 13, 45, tzinfo=UTC)


def _fill(side, qty, price, minutes=0, symbol="DAIC", order_id=None, stop=None):
    return Fill(
        symbol=symbol,
        side=side,
        qty=float(qty),
        price=float(price),
        at=T0 + timedelta(minutes=minutes),
        order_id=order_id or f"{side}-{minutes}",
        stop_loss_price=stop,
    )


# --- pairing ---------------------------------------------------------------


def test_a_long_round_trip_is_one_trade_with_its_pnl():
    closed, open_ = round_trips(
        [_fill("buy", 100, 4.44, 0, stop=4.5), _fill("sell", 100, 4.43, 1)]
    )
    assert open_ == {}
    assert len(closed) == 1
    t = closed[0]
    assert (t.side, t.qty, t.entry_avg, t.exit_avg) == ("long", 100, 4.44, 4.43)
    assert t.pnl == pytest.approx(-1.0)
    assert t.pnl_pct == pytest.approx(-1.0 / 444.0 * 100)
    assert t.opened_at == T0 and t.closed_at == T0 + timedelta(minutes=1)


def test_a_short_round_trip_is_signed_the_other_way():
    closed, _ = round_trips([_fill("sell", 50, 10.0, 0), _fill("buy", 50, 9.0, 5)])
    assert closed[0].side == "short"
    assert closed[0].pnl == pytest.approx(50.0)


def test_scaling_in_and_out_averages_the_legs():
    """Two entries, two exits: entry/exit are value-weighted, and the trip
    closes only when the last share is gone."""
    closed, open_ = round_trips(
        [
            _fill("buy", 100, 10.0, 0),
            _fill("buy", 100, 12.0, 1),
            _fill("sell", 50, 13.0, 2),
            _fill("sell", 150, 11.0, 3),
        ]
    )
    assert open_ == {}
    t = closed[0]
    assert t.qty == 200
    assert t.entry_avg == pytest.approx(11.0)
    assert t.exit_avg == pytest.approx((50 * 13 + 150 * 11) / 200)
    assert t.pnl == pytest.approx(50 * 13 + 150 * 11 - 200 * 11)
    assert t.fill_count == 4
    assert t.exit_order_ids == ["sell-2", "sell-3"]


def test_an_open_position_is_not_a_trade():
    closed, open_ = round_trips(
        [_fill("buy", 100, 10.0, 0), _fill("sell", 40, 11.0, 1)]
    )
    assert closed == []
    assert open_ == {"DAIC": 60.0}


def test_a_fill_through_flat_closes_one_trip_and_opens_the_next():
    """Selling 150 of a 100-share long: 100 close the long, 50 open a short
    from the same order -- and the two trips must not share an id."""
    closed, open_ = round_trips(
        [
            _fill("buy", 100, 10.0, 0, order_id="entry"),
            _fill("sell", 150, 11.0, 1, order_id="flip"),
            _fill("buy", 50, 10.5, 2, order_id="cover"),
        ]
    )
    assert open_ == {}
    assert [t.side for t in closed] == ["long", "short"]
    assert closed[0].pnl == pytest.approx(100.0)
    assert closed[1].pnl == pytest.approx(25.0)
    assert closed[1].entry_order_id == "flip"
    assert len({t.id for t in closed}) == 2


def test_symbols_are_paired_independently():
    closed, _ = round_trips(
        [
            _fill("buy", 10, 1.0, 0, symbol="AAA"),
            _fill("buy", 10, 2.0, 1, symbol="BBB"),
            _fill("sell", 10, 2.0, 2, symbol="AAA"),
            _fill("sell", 10, 1.0, 3, symbol="BBB"),
        ]
    )
    assert {(t.symbol, round(t.pnl, 2)) for t in closed} == {
        ("AAA", 10.0),
        ("BBB", -10.0),
    }


def test_the_r_multiple_is_measured_against_the_initial_stop():
    """The 13:54 DAIC bracket: in at 4.20 with a 4.50 stop (above the fill --
    the bug), out at 4.16. Risk per share is |4.20 - 4.50| = 0.30, so the
    -0.04 loss is -0.13R. A trip with no stop has no R at all."""
    closed, _ = round_trips(
        [_fill("buy", 4756, 4.20, 0, stop=4.5), _fill("sell", 4756, 4.16, 1)]
    )
    t = closed[0]
    assert t.initial_stop == 4.5
    assert t.risk_per_share == pytest.approx(0.30)
    assert t.r_multiple == pytest.approx(-0.04 / 0.30)

    no_stop, _ = round_trips([_fill("buy", 10, 4.20, 0), _fill("sell", 10, 4.16, 1)])
    assert no_stop[0].r_multiple is None and no_stop[0].risk_per_share is None


def test_same_timestamp_fills_pair_deterministically():
    """A bracket entry and its instant stop-out can carry the same
    filled_at; order id breaks the tie the same way every run."""
    fills = [
        _fill("sell", 10, 9.0, 0, order_id="b-stop"),
        _fill("buy", 10, 10.0, 0, order_id="a-entry"),
    ]
    closed, open_ = round_trips(fills)
    assert open_ == {}
    assert closed[0].side == "long" and closed[0].pnl == pytest.approx(-10.0)


# --- summary ---------------------------------------------------------------


def test_summary_counts_wins_losses_and_r():
    rows = [
        {"pnl": 100.0, "r_multiple": 2.0},
        {"pnl": -50.0, "r_multiple": -1.0},
        {"pnl": -25.0, "r_multiple": None},
        {"pnl": 0.0, "r_multiple": 0.0},
    ]
    s = summarize(rows)
    assert (s["count"], s["wins"], s["losses"]) == (4, 1, 2)
    assert s["win_rate"] == pytest.approx(100 / 3)
    assert s["total_pnl"] == pytest.approx(25.0)
    assert s["avg_win"] == 100.0 and s["avg_loss"] == -37.5
    assert s["profit_factor"] == pytest.approx(100 / 75)
    assert s["r_count"] == 3 and s["total_r"] == pytest.approx(1.0)
    assert s["avg_r"] == pytest.approx(1.0 / 3)


def test_summary_of_nothing_has_no_rates():
    s = summarize([])
    assert (
        s["count"] == 0
        and s["win_rate"] is None
        and s["avg_r"] is None
        and s["total_pnl"] == 0
    )


# --- from the broker's order dumps ----------------------------------------


def _order(**overrides):
    order = {
        "id": "o1",
        "symbol": "DAIC",
        "side": "buy",
        "order_type": "limit",
        "status": "filled",
        "filled_qty": "100",
        "filled_avg_price": "4.20",
        "filled_at": "2026-08-25T13:54:08.951308Z",
        "legs": None,
    }
    order.update(overrides)
    return order


def test_fills_come_from_parents_and_their_legs_with_the_stop_attached():
    stop_leg = _order(
        id="leg-stop",
        side="sell",
        order_type="stop",
        stop_price="4.5",
        filled_avg_price="4.16",
        filled_at="2026-08-25T13:54:10.844167Z",
    )
    tp_leg = _order(
        id="leg-tp",
        side="sell",
        order_type="limit",
        limit_price="5.17",
        status="canceled",
        filled_qty="0",
        filled_avg_price=None,
        filled_at=None,
    )
    parent = _order(id="parent", legs=[tp_leg, stop_leg])

    fills = fills_from_orders([parent, stop_leg])  # the leg listed twice
    assert [f.order_id for f in fills] == ["parent", "leg-stop"]
    assert fills[0].stop_loss_price == 4.5
    assert fills[1].stop_loss_price is None
    assert fills[0].at.tzinfo is not None

    closed, _ = round_trips(fills)
    assert closed[0].r_multiple == pytest.approx(-0.04 / 0.30)


def test_unfilled_and_malformed_orders_produce_no_fill():
    assert (
        fills_from_orders(
            [_order(status="canceled", filled_qty="0", filled_avg_price=None)]
        )
        == []
    )
    assert fills_from_orders([_order(filled_at=None)]) == []
    assert fills_from_orders([_order(id="")]) == []
    assert fills_from_orders(["not a dict", None]) == []


# --- the record ------------------------------------------------------------


def _store(tmp_path) -> TradeStore:
    store = TradeStore(str(tmp_path / "history.sqlite3"))
    asyncio.run(store.init_schema())
    return store


def test_store_round_trips_and_upserts(tmp_path):
    store = _store(tmp_path)
    closed, _ = round_trips(
        [_fill("buy", 100, 10.0, 0, stop=9.0), _fill("sell", 100, 11.0, 1)]
    )
    asyncio.run(store.upsert(closed))
    asyncio.run(store.upsert(closed))  # again: still one row

    rows = asyncio.run(store.all())
    assert len(rows) == 1
    assert rows[0]["id"] == closed[0].id
    assert rows[0]["pnl"] == pytest.approx(100.0)
    assert rows[0]["r_multiple"] == pytest.approx(1.0)
    assert rows[0]["exit_order_ids"] == ["sell-1"]

    # A later pass that sees more of the trip corrects the row in place.
    corrected, _ = round_trips(
        [
            _fill("buy", 100, 10.0, 0, stop=9.0),
            _fill("sell", 60, 11.0, 1),
            _fill("sell", 40, 12.0, 2),
        ]
    )
    asyncio.run(store.upsert(corrected))
    rows = asyncio.run(store.all())
    assert len(rows) == 1 and rows[0]["pnl"] == pytest.approx(60 + 80)


def test_store_lists_newest_close_first(tmp_path):
    store = _store(tmp_path)
    closed, _ = round_trips(
        [
            _fill("buy", 1, 1.0, 0, symbol="AAA"),
            _fill("sell", 1, 2.0, 1, symbol="AAA"),
            _fill("buy", 1, 1.0, 2, symbol="BBB"),
            _fill("sell", 1, 2.0, 3, symbol="BBB"),
        ]
    )
    asyncio.run(store.upsert(closed))
    assert [r["symbol"] for r in asyncio.run(store.all())] == ["BBB", "AAA"]


# --- the service: broker in, record out -----------------------------------


class _StubTradingClient:
    def __init__(self, orders):
        self._orders = orders
        self.requests = []

    def get_orders(self, request):
        self.requests.append(request)
        return self._orders


def test_sync_trades_pairs_persists_and_reports(tmp_path):
    entry = _order(
        id="entry",
        legs=[
            _order(
                id="stop",
                side="sell",
                order_type="stop",
                stop_price="4.5",
                filled_avg_price="4.16",
                filled_at="2026-08-25T13:54:10Z",
            )
        ],
    )
    still_open = _order(
        id="open",
        symbol="XYZ",
        filled_avg_price="1.00",
        filled_at="2026-08-25T14:00:00Z",
    )
    client = _StubTradingClient([entry, still_open])
    clients = type("C", (), {"trading": client, "feed": "sip"})()
    service = OrderService(
        clients=clients,
        settings=Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s"),
    )
    store = _store(tmp_path)

    result = asyncio.run(service.sync_trades(store))

    assert client.requests[0].nested is True
    assert [t["symbol"] for t in result["trades"]] == ["DAIC"]
    assert result["trades"][0]["pnl"] == pytest.approx(-4.0)
    assert result["summary"]["count"] == 1 and result["summary"]["losses"] == 1
    assert result["open_symbols"] == ["XYZ"]
    # Persisted: a second sync against an empty broker still lists it.
    client._orders = []
    again = asyncio.run(service.sync_trades(store))
    assert [t["symbol"] for t in again["trades"]] == ["DAIC"]
