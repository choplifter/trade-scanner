"""Persistent state for the simulated options book -- the option side of
app.trading.sim.store.SimStore, same file, same conventions (stdlib
sqlite3, a fresh connection per call via asyncio.to_thread, WAL mode,
everything scoped by user_id).

Two tables. sim_option_orders holds a whole package per row -- the legs
as JSON, the way Alpaca's own multi-leg order carries its legs -- because
a spread fills or rests as one thing. sim_option_positions is one row per
contract, the exact column set sim_positions has, so the equity broker's
pure fill arithmetic (app.trading.sim.broker._apply_fill_to_position)
runs over an option position unchanged; the contract multiplier is
applied to money afterwards, in the book. Closed round trips go to
sim_trades with multiplier 100, next to the stock trades.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_option_orders (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    client_order_id TEXT,
    underlying TEXT NOT NULL,
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,
    qty INTEGER NOT NULL,
    filled_qty INTEGER NOT NULL DEFAULT 0,
    limit_price REAL,
    legs TEXT NOT NULL,
    status TEXT NOT NULL,
    net_fill_price REAL,
    source TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    filled_at TEXT,
    canceled_at TEXT,
    as_of TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_option_orders_user_status ON sim_option_orders(user_id, status);

CREATE TABLE IF NOT EXISTS sim_option_positions (
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    avg_entry_price REAL NOT NULL,
    opened_at TEXT NOT NULL,
    entry_order_id TEXT NOT NULL,
    initial_stop REAL,
    exit_qty REAL NOT NULL DEFAULT 0,
    exit_value REAL NOT NULL DEFAULT 0,
    exit_order_ids TEXT NOT NULL DEFAULT '[]',
    fill_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, symbol)
);
"""

ORDER_COLUMNS = (
    "id", "client_order_id", "underlying", "strategy", "direction", "qty", "filled_qty",
    "limit_price", "legs", "status", "net_fill_price", "source", "submitted_at",
    "filled_at", "canceled_at", "as_of",
)

_POSITION_COLUMNS = (
    "symbol", "side", "qty", "avg_entry_price", "opened_at", "entry_order_id",
    "initial_stop", "exit_qty", "exit_value", "exit_order_ids", "fill_count",
)

# A resting package waits for its limit; everything else is final.
WORKING = "new"
FILLED = "filled"
CANCELED = "canceled"
EXPIRED = "expired"
_CLOSED_STATUSES = (FILLED, CANCELED, EXPIRED)


def _row_to_order(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["legs"] = json.loads(d["legs"])
    except (TypeError, ValueError):
        d["legs"] = []
    return d


def _row_to_position(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["exit_order_ids"] = json.loads(d["exit_order_ids"])
    except (TypeError, ValueError):
        d["exit_order_ids"] = []
    return d


class SimOptionsStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    def _reset_sync(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sim_option_positions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sim_option_orders WHERE user_id = ?", (user_id,))

    async def reset(self, user_id: int) -> None:
        await asyncio.to_thread(self._reset_sync, user_id)

    # --- orders ---------------------------------------------------------------

    def _insert_order_sync(self, user_id: int, order: dict) -> None:
        d = dict(order)
        d["legs"] = json.dumps(d.get("legs") or [])
        columns = ("user_id", *ORDER_COLUMNS)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO sim_option_orders ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                (user_id, *(d.get(c) for c in ORDER_COLUMNS)),
            )

    async def insert_order(self, user_id: int, order: dict) -> None:
        await asyncio.to_thread(self._insert_order_sync, user_id, order)

    def _get_order_sync(self, user_id: int, order_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(ORDER_COLUMNS)} FROM sim_option_orders WHERE id = ? AND user_id = ?",
                (order_id, user_id),
            ).fetchone()
        return _row_to_order(row) if row else None

    async def get_order(self, user_id: int, order_id: str) -> dict | None:
        return await asyncio.to_thread(self._get_order_sync, user_id, order_id)

    def _update_order_sync(self, user_id: int, order_id: str, fields: dict) -> None:
        d = dict(fields)
        if "legs" in d:
            d["legs"] = json.dumps(d["legs"])
        assignments = ", ".join(f"{k} = ?" for k in d)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE sim_option_orders SET {assignments} WHERE id = ? AND user_id = ?",
                (*d.values(), order_id, user_id),
            )

    async def update_order(self, user_id: int, order_id: str, **fields) -> None:
        await asyncio.to_thread(self._update_order_sync, user_id, order_id, fields)

    def _list_orders_sync(self, user_id: int, status: str) -> list[dict]:
        with self._connect() as conn:
            if status == "open":
                rows = conn.execute(
                    f"SELECT {', '.join(ORDER_COLUMNS)} FROM sim_option_orders "
                    "WHERE user_id = ? AND status = ? ORDER BY submitted_at DESC",
                    (user_id, WORKING),
                ).fetchall()
            elif status == "closed":
                placeholders = ", ".join("?" for _ in _CLOSED_STATUSES)
                rows = conn.execute(
                    f"SELECT {', '.join(ORDER_COLUMNS)} FROM sim_option_orders "
                    f"WHERE user_id = ? AND status IN ({placeholders}) ORDER BY submitted_at DESC",
                    (user_id, *_CLOSED_STATUSES),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {', '.join(ORDER_COLUMNS)} FROM sim_option_orders "
                    "WHERE user_id = ? ORDER BY submitted_at DESC",
                    (user_id,),
                ).fetchall()
        return [_row_to_order(r) for r in rows]

    async def list_orders(self, user_id: int, status: str = "open") -> list[dict]:
        return await asyncio.to_thread(self._list_orders_sync, user_id, status)

    async def working_orders(self, user_id: int) -> list[dict]:
        return await self.list_orders(user_id, "open")

    def _users_with_work_sync(self) -> set[int]:
        """Every user with a resting package or an open contract -- who the
        live fill loop has to look at."""
        with self._connect() as conn:
            orders = conn.execute(
                "SELECT DISTINCT user_id FROM sim_option_orders WHERE status = ?", (WORKING,)
            ).fetchall()
            positions = conn.execute("SELECT DISTINCT user_id FROM sim_option_positions").fetchall()
        return {int(r[0]) for r in orders} | {int(r[0]) for r in positions}

    async def users_with_work(self) -> set[int]:
        return await asyncio.to_thread(self._users_with_work_sync)

    # --- positions --------------------------------------------------------------

    def _list_positions_sync(self, user_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_POSITION_COLUMNS)} FROM sim_option_positions WHERE user_id = ? ORDER BY symbol",
                (user_id,),
            ).fetchall()
        return [_row_to_position(r) for r in rows]

    async def list_positions(self, user_id: int) -> list[dict]:
        return await asyncio.to_thread(self._list_positions_sync, user_id)

    def _get_position_sync(self, user_id: int, symbol: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_POSITION_COLUMNS)} FROM sim_option_positions WHERE user_id = ? AND symbol = ?",
                (user_id, symbol),
            ).fetchone()
        return _row_to_position(row) if row else None

    async def get_position(self, user_id: int, symbol: str) -> dict | None:
        return await asyncio.to_thread(self._get_position_sync, user_id, symbol)

    def _upsert_position_sync(self, user_id: int, position: dict) -> None:
        d = dict(position)
        d["exit_order_ids"] = json.dumps(d.get("exit_order_ids") or [])
        columns = ("user_id", *_POSITION_COLUMNS)
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _POSITION_COLUMNS if c != "symbol")
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO sim_option_positions ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
                f"ON CONFLICT(user_id, symbol) DO UPDATE SET {assignments}",
                (user_id, *(d[c] for c in _POSITION_COLUMNS)),
            )

    async def upsert_position(self, user_id: int, position: dict) -> None:
        await asyncio.to_thread(self._upsert_position_sync, user_id, position)

    def _delete_position_sync(self, user_id: int, symbol: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sim_option_positions WHERE user_id = ? AND symbol = ?", (user_id, symbol))

    async def delete_position(self, user_id: int, symbol: str) -> None:
        await asyncio.to_thread(self._delete_position_sync, user_id, symbol)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
