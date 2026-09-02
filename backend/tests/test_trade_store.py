"""TradeStore keeps paper and live trips apart, and grows the column on a
database created before there was a live account."""

import asyncio
import sqlite3
from datetime import UTC, datetime

from app.trading.trade_store import TradeStore
from app.trading.trades import Trade


def _trade(symbol: str, entry_order_id: str) -> Trade:
    return Trade(
        symbol=symbol,
        side="long",
        opened_at=datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
        closed_at=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
        qty=10,
        entry_avg=10.0,
        exit_avg=11.0,
        pnl=10.0,
        pnl_pct=10.0,
        initial_stop=None,
        risk_per_share=None,
        r_multiple=None,
        entry_order_id=entry_order_id,
        exit_order_ids=["x1"],
        fill_count=2,
    )


def test_trips_are_kept_per_account(tmp_path):
    store = TradeStore(str(tmp_path / "t.sqlite3"))
    asyncio.run(store.init_schema())
    asyncio.run(store.upsert([_trade("AAA", "p1")]))
    asyncio.run(store.upsert([_trade("BBB", "l1")], account="live"))

    assert [r["symbol"] for r in asyncio.run(store.all())] == ["AAA"]
    assert [r["symbol"] for r in asyncio.run(store.all(account="live"))] == ["BBB"]


def test_schema_migration_adds_the_account_column(tmp_path):
    """A database from before the live account has no `account` column;
    init_schema adds it and existing rows read as paper."""
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE trades (
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
                opened_at TEXT NOT NULL, closed_at TEXT NOT NULL, qty REAL NOT NULL,
                entry_avg REAL NOT NULL, exit_avg REAL NOT NULL, pnl REAL NOT NULL,
                pnl_pct REAL, initial_stop REAL, risk_per_share REAL, r_multiple REAL,
                entry_order_id TEXT NOT NULL, exit_order_ids TEXT NOT NULL,
                fill_count INTEGER NOT NULL, recorded_at TEXT NOT NULL
            );
            INSERT INTO trades VALUES ('AAA:p1:1','AAA','long','2026-09-01T14:00:00+00:00',
                '2026-09-01T15:00:00+00:00',10,10,11,10,10,NULL,NULL,NULL,'p1','["x1"]',2,'now');
            """
        )
    store = TradeStore(str(path))
    asyncio.run(store.init_schema())
    rows = asyncio.run(store.all())
    assert [r["symbol"] for r in rows] == ["AAA"]
    assert asyncio.run(store.all(account="live")) == []
