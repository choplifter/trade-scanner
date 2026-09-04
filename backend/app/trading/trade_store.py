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
    recorded_at TEXT NOT NULL,
    account TEXT NOT NULL DEFAULT 'paper',
    multiplier INTEGER NOT NULL DEFAULT 1,
    user_id INTEGER NOT NULL DEFAULT 0
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
    "account",
    "multiplier",
    "user_id",
)


class TradeStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # Databases from before the live account: the column is added
            # in place and every existing trip reads as paper, which is the
            # only account that existed then.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
            if "account" not in columns:
                conn.execute("ALTER TABLE trades ADD COLUMN account TEXT NOT NULL DEFAULT 'paper'")
            # Shares per unit of qty (100 for an option contract); rows from
            # before it existed are stocks, or get corrected by the next
            # sync's upsert.
            if "multiplier" not in columns:
                conn.execute("ALTER TABLE trades ADD COLUMN multiplier INTEGER NOT NULL DEFAULT 1")
            # Whose broker account the trip was on (per-user keys, see
            # app.broker). Rows from before are the operator's: user_id 0,
            # read back only for the admin (see all()).
            if "user_id" not in columns:
                conn.execute("ALTER TABLE trades ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_account_closed_at ON trades(account, closed_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_user_account_closed_at ON trades(user_id, account, closed_at)"
            )

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    def _upsert_sync(self, trades: list[Trade], account: str, user_id: int, now: datetime) -> None:
        # Upsert rather than insert-or-ignore: the closed-orders window
        # slides, and a trip first seen with only some of its fills (a
        # partial exit at the window's edge) is corrected on the next pass.
        rows = []
        for t in trades:
            d = t.to_dict()
            d["exit_order_ids"] = json.dumps(d["exit_order_ids"])
            d["account"] = account
            d["user_id"] = user_id
            rows.append(tuple(d[c] for c in _COLUMNS) + (now.isoformat(),))
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "id")
        with self._connect() as conn:
            conn.executemany(
                f"INSERT INTO trades ({', '.join(_COLUMNS)}, recorded_at) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)}, ?) "
                f"ON CONFLICT(id) DO UPDATE SET {assignments}",
                rows,
            )

    async def upsert(
        self, trades: list[Trade], now: datetime | None = None, *, account: str = "paper", user_id: int = 0
    ) -> None:
        if not trades:
            return
        await asyncio.to_thread(self._upsert_sync, trades, account, user_id, now or datetime.now(UTC))

    def _all_sync(self, account: str, user_id: int, include_legacy: bool) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM trades WHERE account = ? "
                "AND (user_id = ? OR (? AND user_id = 0)) "
                "ORDER BY closed_at DESC, id",
                (account, user_id, 1 if include_legacy else 0),
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

    async def all(self, account: str = "paper", user_id: int = 0, include_legacy: bool = True) -> list[dict]:
        """Every recorded trip of one user's account, newest close first.
        `include_legacy` adds the rows recorded before trips were per user
        (user_id 0) -- the admin's, since the .env keys were the only
        account then."""
        return await asyncio.to_thread(self._all_sync, account, user_id, include_legacy)
