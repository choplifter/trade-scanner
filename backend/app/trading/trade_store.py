"""Persistent (SQLite) record of closed round trips.

The broker's own history is the source, but it is not a record: a paper
reset wipes it, and the closed-orders query is capped at 500 rows. Every
trip that has ever been paired is kept here, so "which trade lost what"
survives both. Same file as the scanner history (one thing to back up),
same conventions as ScannerHistoryStore: stdlib sqlite3, a fresh connection
per call, every call via asyncio.to_thread.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime

from app.trading.trades import Trade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_avg REAL NOT NULL,
    exit_avg REAL NOT NULL,
    pnl REAL NOT NULL,
    pnl_pct REAL,
    initial_stop REAL,
    risk_per_share REAL,
    r_multiple REAL,
    entry_order_id TEXT NOT NULL,
    exit_order_ids TEXT NOT NULL,
    fill_count INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);
"""

_COLUMNS = (
    "id",
    "symbol",
    "side",
    "opened_at",
    "closed_at",
    "qty",
    "entry_avg",
    "exit_avg",
    "pnl",
    "pnl_pct",
    "initial_stop",
    "risk_per_share",
    "r_multiple",
    "entry_order_id",
    "exit_order_ids",
    "fill_count",
)


class TradeStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    def _upsert_sync(self, trades: list[Trade], now: datetime) -> None:
        # Upsert rather than insert-or-ignore: the closed-orders window
        # slides, and a trip first seen with only some of its fills (a
        # partial exit at the window's edge) is corrected on the next pass.
        rows = []
        for t in trades:
            d = t.to_dict()
            d["exit_order_ids"] = json.dumps(d["exit_order_ids"])
            rows.append(tuple(d[c] for c in _COLUMNS) + (now.isoformat(),))
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "id")
        with self._connect() as conn:
            conn.executemany(
                f"INSERT INTO trades ({', '.join(_COLUMNS)}, recorded_at) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)}, ?) "
                f"ON CONFLICT(id) DO UPDATE SET {assignments}",
                rows,
            )

    async def upsert(self, trades: list[Trade], now: datetime | None = None) -> None:
        if not trades:
            return
        await asyncio.to_thread(self._upsert_sync, trades, now or datetime.now(UTC))

    def _all_sync(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM trades ORDER BY closed_at DESC, id"
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            try:
                d["exit_order_ids"] = json.loads(d["exit_order_ids"])
            except (TypeError, ValueError):
                d["exit_order_ids"] = []
            out.append(d)
        return out

    async def all(self) -> list[dict]:
        """Every recorded trip, newest close first."""
        return await asyncio.to_thread(self._all_sync)
