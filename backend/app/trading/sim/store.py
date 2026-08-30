"""Persistent state for Simulation Mode's local order book.

Same file as the scanner history and the real trade record
(settings.scanner_history_db_path -- one thing to back up), same
conventions as TradeStore/ScannerHistoryStore: stdlib sqlite3, a fresh
connection per call via asyncio.to_thread, WAL mode (a one-time,
persistent property of the file, so setting it on every connect just
confirms it's already on -- see ScannerHistoryStore._connect).

This module only ever reads/writes sim_* tables. It never touches
alpaca_clients.trading -- the whole point of Simulation Mode is a broker
the app owns, not a proxy to Alpaca's paper account.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    starting_cash REAL NOT NULL,
    created_at TEXT NOT NULL,
    reset_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_positions (
    symbol TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    avg_entry_price REAL NOT NULL,
    opened_at TEXT NOT NULL,
    entry_order_id TEXT NOT NULL,
    initial_stop REAL,
    exit_qty REAL NOT NULL DEFAULT 0,
    exit_value REAL NOT NULL DEFAULT 0,
    exit_order_ids TEXT NOT NULL DEFAULT '[]',
    fill_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sim_orders (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    oco_group_id TEXT,
    leg_role TEXT,
    client_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    order_class TEXT NOT NULL,
    qty REAL NOT NULL,
    filled_qty REAL NOT NULL DEFAULT 0,
    limit_price REAL,
    stop_price REAL,
    time_in_force TEXT NOT NULL,
    status TEXT NOT NULL,
    filled_avg_price REAL,
    submitted_at TEXT NOT NULL,
    filled_at TEXT,
    canceled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_orders_symbol_status ON sim_orders(symbol, status);
CREATE INDEX IF NOT EXISTS idx_sim_orders_parent ON sim_orders(parent_id);
CREATE INDEX IF NOT EXISTS idx_sim_orders_oco ON sim_orders(oco_group_id);

CREATE TABLE IF NOT EXISTS sim_trades (
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
CREATE INDEX IF NOT EXISTS idx_sim_trades_closed_at ON sim_trades(closed_at);
"""

_ORDER_COLUMNS = (
    "id", "parent_id", "oco_group_id", "leg_role", "client_order_id",
    "symbol", "side", "order_type", "order_class", "qty", "filled_qty",
    "limit_price", "stop_price", "time_in_force", "status",
    "filled_avg_price", "submitted_at", "filled_at", "canceled_at",
)

_POSITION_COLUMNS = (
    "symbol", "side", "qty", "avg_entry_price", "opened_at", "entry_order_id",
    "initial_stop", "exit_qty", "exit_value", "exit_order_ids", "fill_count",
)

_TRADE_COLUMNS = (
    "id", "symbol", "side", "opened_at", "closed_at", "qty", "entry_avg",
    "exit_avg", "pnl", "pnl_pct", "initial_stop", "risk_per_share",
    "r_multiple", "entry_order_id", "exit_order_ids", "fill_count",
)

# Mirrors OrderService's _WORKING_STATUSES concept: a "held" bracket child
# is not yet live but is still working in the sense a cancel must reach it.
_OPEN_STATUSES = ("new", "held")
_CLOSED_STATUSES = ("filled", "canceled")


class SimStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        return conn

    # --- schema / lifecycle --------------------------------------------

    def _init_schema_sync(self, starting_cash: float, now: datetime) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            existing = conn.execute("SELECT id FROM sim_account WHERE id = 1").fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO sim_account (id, cash, starting_cash, created_at, reset_at) "
                    "VALUES (1, ?, ?, ?, ?)",
                    (starting_cash, starting_cash, now.isoformat(), now.isoformat()),
                )

    async def init_schema(self, starting_cash: float) -> None:
        await asyncio.to_thread(self._init_schema_sync, starting_cash, datetime.now(UTC))

    def _reset_sync(self, starting_cash: float, now: datetime) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sim_positions")
            conn.execute("DELETE FROM sim_orders")
            conn.execute("DELETE FROM sim_trades")
            conn.execute(
                "UPDATE sim_account SET cash = ?, starting_cash = ?, reset_at = ? WHERE id = 1",
                (starting_cash, starting_cash, now.isoformat()),
            )

    async def reset(self, starting_cash: float) -> None:
        await asyncio.to_thread(self._reset_sync, starting_cash, datetime.now(UTC))

    # --- account ---------------------------------------------------------

    def _get_account_row_sync(self) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cash, starting_cash, created_at, reset_at FROM sim_account WHERE id = 1"
            ).fetchone()
        return dict(row)

    async def get_account_row(self) -> dict:
        return await asyncio.to_thread(self._get_account_row_sync)

    def _add_cash_sync(self, delta: float) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE sim_account SET cash = cash + ? WHERE id = 1", (delta,))

    async def add_cash(self, delta: float) -> None:
        await asyncio.to_thread(self._add_cash_sync, delta)

    # --- positions ---------------------------------------------------------

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["exit_order_ids"] = json.loads(d["exit_order_ids"])
        except (TypeError, ValueError):
            d["exit_order_ids"] = []
        return d

    def _list_positions_sync(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT {', '.join(_POSITION_COLUMNS)} FROM sim_positions").fetchall()
        return [self._row_to_position(r) for r in rows]

    async def list_positions(self) -> list[dict]:
        return await asyncio.to_thread(self._list_positions_sync)

    def _get_position_sync(self, symbol: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_POSITION_COLUMNS)} FROM sim_positions WHERE symbol = ?", (symbol,)
            ).fetchone()
        return self._row_to_position(row) if row else None

    async def get_position(self, symbol: str) -> dict | None:
        return await asyncio.to_thread(self._get_position_sync, symbol)

    def _upsert_position_sync(self, position: dict) -> None:
        d = dict(position)
        d["exit_order_ids"] = json.dumps(d.get("exit_order_ids") or [])
        placeholders = ", ".join("?" for _ in _POSITION_COLUMNS)
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _POSITION_COLUMNS if c != "symbol")
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO sim_positions ({', '.join(_POSITION_COLUMNS)}) VALUES ({placeholders}) "
                f"ON CONFLICT(symbol) DO UPDATE SET {assignments}",
                tuple(d[c] for c in _POSITION_COLUMNS),
            )

    async def upsert_position(self, position: dict) -> None:
        await asyncio.to_thread(self._upsert_position_sync, position)

    def _delete_position_sync(self, symbol: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sim_positions WHERE symbol = ?", (symbol,))

    async def delete_position(self, symbol: str) -> None:
        await asyncio.to_thread(self._delete_position_sync, symbol)

    # --- orders ---------------------------------------------------------

    def _insert_order_sync(self, order: dict) -> None:
        placeholders = ", ".join("?" for _ in _ORDER_COLUMNS)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO sim_orders ({', '.join(_ORDER_COLUMNS)}) VALUES ({placeholders})",
                tuple(order.get(c) for c in _ORDER_COLUMNS),
            )

    async def insert_order(self, order: dict) -> None:
        await asyncio.to_thread(self._insert_order_sync, order)

    def _get_order_sync(self, order_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders WHERE id = ?", (order_id,)
            ).fetchone()
        return dict(row) if row else None

    async def get_order(self, order_id: str) -> dict | None:
        return await asyncio.to_thread(self._get_order_sync, order_id)

    def _update_order_sync(self, order_id: str, fields: dict) -> None:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE sim_orders SET {assignments} WHERE id = ?",
                (*fields.values(), order_id),
            )

    async def update_order(self, order_id: str, **fields) -> None:
        await asyncio.to_thread(self._update_order_sync, order_id, fields)

    def _list_orders_sync(self, status: str) -> list[dict]:
        with self._connect() as conn:
            if status == "open":
                placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
                rows = conn.execute(
                    f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders "
                    f"WHERE status IN ({placeholders}) ORDER BY submitted_at DESC",
                    _OPEN_STATUSES,
                ).fetchall()
            elif status == "closed":
                placeholders = ", ".join("?" for _ in _CLOSED_STATUSES)
                rows = conn.execute(
                    f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders "
                    f"WHERE status IN ({placeholders}) ORDER BY submitted_at DESC",
                    _CLOSED_STATUSES,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders ORDER BY submitted_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    async def list_orders(self, status: str = "open") -> list[dict]:
        return await asyncio.to_thread(self._list_orders_sync, status)

    def _working_orders_by_symbol_sync(self) -> dict[str, list[dict]]:
        with self._connect() as conn:
            placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
            rows = conn.execute(
                f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders WHERE status IN ({placeholders})",
                _OPEN_STATUSES,
            ).fetchall()
        by_symbol: dict[str, list[dict]] = {}
        for row in rows:
            d = dict(row)
            by_symbol.setdefault(d["symbol"], []).append(d)
        return by_symbol

    async def working_orders_by_symbol(self) -> dict[str, list[dict]]:
        return await asyncio.to_thread(self._working_orders_by_symbol_sync)

    def _cancel_oco_siblings_sync(self, oco_group_id: str, exclude_order_id: str, now: str) -> None:
        with self._connect() as conn:
            placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
            conn.execute(
                f"UPDATE sim_orders SET status = 'canceled', canceled_at = ? "
                f"WHERE oco_group_id = ? AND id != ? AND status IN ({placeholders})",
                (now, oco_group_id, exclude_order_id, *_OPEN_STATUSES),
            )

    async def cancel_oco_siblings(self, oco_group_id: str, exclude_order_id: str, now: datetime) -> None:
        await asyncio.to_thread(self._cancel_oco_siblings_sync, oco_group_id, exclude_order_id, now.isoformat())

    def _activate_children_sync(self, parent_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sim_orders SET status = 'new' WHERE parent_id = ? AND status = 'held'",
                (parent_id,),
            )

    async def activate_children(self, parent_id: str) -> None:
        await asyncio.to_thread(self._activate_children_sync, parent_id)

    def _cancel_children_sync(self, parent_id: str, now: str) -> None:
        with self._connect() as conn:
            placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
            conn.execute(
                f"UPDATE sim_orders SET status = 'canceled', canceled_at = ? "
                f"WHERE parent_id = ? AND status IN ({placeholders})",
                (now, parent_id, *_OPEN_STATUSES),
            )

    async def cancel_children(self, parent_id: str, now: datetime) -> None:
        await asyncio.to_thread(self._cancel_children_sync, parent_id, now.isoformat())

    def _child_stop_price_sync(self, parent_id: str) -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT stop_price FROM sim_orders WHERE parent_id = ? AND leg_role = 'stop_loss'",
                (parent_id,),
            ).fetchone()
        return float(row["stop_price"]) if row and row["stop_price"] is not None else None

    async def child_stop_price(self, parent_id: str) -> float | None:
        return await asyncio.to_thread(self._child_stop_price_sync, parent_id)

    # --- trades ---------------------------------------------------------

    def _insert_trade_sync(self, trade: dict, now: str) -> None:
        d = dict(trade)
        d["exit_order_ids"] = json.dumps(d.get("exit_order_ids") or [])
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO sim_trades ({', '.join(_TRADE_COLUMNS)}, recorded_at) "
                f"VALUES ({', '.join('?' for _ in _TRADE_COLUMNS)}, ?)",
                (*(d[c] for c in _TRADE_COLUMNS), now),
            )

    async def insert_trade(self, trade: dict) -> None:
        await asyncio.to_thread(self._insert_trade_sync, trade, datetime.now(UTC).isoformat())

    def _list_trades_sync(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_TRADE_COLUMNS)} FROM sim_trades ORDER BY closed_at DESC, id"
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

    async def list_trades(self) -> list[dict]:
        return await asyncio.to_thread(self._list_trades_sync)
