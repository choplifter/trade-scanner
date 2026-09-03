"""OptionStreamManager batches a contract's prints into one trade message
per window and forwards the newest quote, on the chart topic."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.market_data import option_stream_manager as osm
from app.market_data.option_stream_manager import OptionStreamManager


@dataclass
class _Broadcasts:
    sent: list = field(default_factory=list)

    async def broadcast(self, topic, message):
        self.sent.append((topic, message))


@dataclass
class _Trade:
    symbol: str
    price: float
    size: int
    timestamp: datetime


@dataclass
class _Quote:
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: int
    ask_size: int
    timestamp: datetime


def _manager(monkeypatch) -> tuple[OptionStreamManager, _Broadcasts]:
    monkeypatch.setattr(osm, "TRADE_FLUSH_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(osm, "QUOTE_FLUSH_INTERVAL_SECONDS", 0.01)
    sink = _Broadcasts()
    return OptionStreamManager(clients=None, manager=sink, max_symbols=5), sink  # type: ignore[arg-type]


def test_trades_fold_into_one_message_per_window(monkeypatch):
    manager, sink = _manager(monkeypatch)

    async def run():
        t = datetime(2026, 9, 3, 15, 45, 12, tzinfo=timezone.utc)
        await manager._on_trade(_Trade("SPY260904C00765000", 3.10, 1, t))
        await manager._on_trade(_Trade("SPY260904C00765000", 3.25, 2, t.replace(second=30)))
        await manager._on_trade(_Trade("SPY260904C00765000", 3.05, 1, t.replace(second=40)))
        await asyncio.sleep(0.05)

    asyncio.run(run())
    (topic, msg), = sink.sent
    assert topic == "chart:SPY260904C00765000"
    assert msg["type"] == "trade" and msg["t"] == "2026-09-03T15:45:00+00:00"
    assert (msg["o"], msg["h"], msg["l"], msg["c"], msg["v"]) == (3.10, 3.25, 3.05, 3.05, 4.0)


def test_only_the_newest_quote_of_a_window_goes_out(monkeypatch):
    manager, sink = _manager(monkeypatch)

    async def run():
        t = datetime(2026, 9, 3, 15, 45, 12, tzinfo=timezone.utc)
        await manager._on_quote(_Quote("SPY260904C00765000", 3.0, 3.2, 10, 12, t))
        await manager._on_quote(_Quote("SPY260904C00765000", 3.05, 3.15, 8, 9, t.replace(second=13)))
        await asyncio.sleep(0.05)

    asyncio.run(run())
    (topic, msg), = sink.sent
    assert topic == "chart:SPY260904C00765000" and msg["type"] == "quote"
    assert (msg["bid"], msg["ask"], msg["bid_size"], msg["ask_size"]) == (3.05, 3.15, 8.0, 9.0)


def test_unsubscribe_of_an_unknown_symbol_is_a_no_op(monkeypatch):
    manager, _sink = _manager(monkeypatch)
    asyncio.run(manager.unsubscribe("SPY260904C00765000"))
