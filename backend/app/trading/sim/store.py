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

Every table is scoped by user_id (see app.auth) -- each logged-in user gets
their own account/positions/orders/trades, never another's. sim_account's
PK is user_id itself (one row per user, created lazily on first touch);
sim_orders/sim_trades keep their own globally-unique id (a uuid) as PK and
carry user_id as an indexed column instead, since nothing about those ids
needs to be user-scoped to stay unique.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_account (
    user_id INTEGER PRIMARY KEY,
    cash REAL NOT NULL,
    starting_cash REAL NOT NULL,
    created_at TEXT NOT NULL,
    reset_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_positions (
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

CREATE TABLE IF NOT EXISTS sim_orders (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_sim_orders_user_status ON sim_orders(user_id, status);
CREATE INDEX IF NOT EXISTS idx_sim_orders_parent ON sim_orders(parent_id);
CREATE INDEX IF NOT EXISTS idx_sim_orders_oco ON sim_orders(oco_group_id);

CREATE TABLE IF NOT EXISTS sim_trades (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
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
    multiplier INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_sim_trades_user_closed_at ON sim_trades(user_id, closed_at);
"""

# Columns added after the table first shipped (see _init_schema_sync):
# multiplier is 100 for an option contract's round trip, 1 for shares.
_ADDED_TRADE_COLUMNS = {"multiplier": "INTEGER NOT NULL DEFAULT 1"}

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
    "r_multiple", "entry_order_id", "exit_order_ids", "fill_count", "multiplier",
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

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            present = {row[1] for row in conn.execute("PRAGMA table_info(sim_trades)")}
            for column, kind in _ADDED_TRADE_COLUMNS.items():
                if column not in present:
                    conn.execute(f"ALTER TABLE sim_trades ADD COLUMN {column} {kind}")

    async def init_schema(self) -> None:
        """No longer seeds a singleton account row -- each user's
        sim_account row is created lazily on first touch (see
        ensure_account), since users are created after the app starts."""
        await asyncio.to_thread(self._init_schema_sync)

    def _ensure_account_sync(self, user_id: int, starting_cash: float, now: datetime) -> None:
        with self._connect() as conn:
            existing = conn.execute("SELECT user_id FROM sim_account WHERE user_id = ?", (user_id,)).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO sim_account (user_id, cash, starting_cash, created_at, reset_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, starting_cash, starting_cash, now.isoformat(), now.isoformat()),
                )

    async def ensure_account(self, user_id: int, starting_cash: float) -> None:
        await asyncio.to_thread(self._ensure_account_sync, user_id, starting_cash, datetime.now(UTC))

    def _reset_sync(self, user_id: int, starting_cash: float, now: datetime) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sim_positions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sim_orders WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sim_trades WHERE user_id = ?", (user_id,))
            conn.execute(
                "UPDATE sim_account SET cash = ?, starting_cash = ?, reset_at = ? WHERE user_id = ?",
                (starting_cash, starting_cash, now.isoformat(), user_id),
            )

    async def reset(self, user_id: int, starting_cash: float) -> None:
        await asyncio.to_thread(self._reset_sync, user_id, starting_cash, datetime.now(UTC))

    # --- account ---------------------------------------------------------

    def _get_account_row_sync(self, user_id: int) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cash, starting_cash, created_at, reset_at FROM sim_account WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row)

    async def get_account_row(self, user_id: int) -> dict:
        return await asyncio.to_thread(self._get_account_row_sync, user_id)

    def _add_cash_sync(self, user_id: int, delta: float) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE sim_account SET cash = cash + ? WHERE user_id = ?", (delta, user_id))

    async def add_cash(self, user_id: int, delta: float) -> None:
        await asyncio.to_thread(self._add_cash_sync, user_id, delta)

    # --- positions ---------------------------------------------------------

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["exit_order_ids"] = json.loads(d["exit_order_ids"])
        except (TypeError, ValueError):
            d["exit_order_ids"] = []
        return d

    def _list_positions_sync(self, user_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_POSITION_COLUMNS)} FROM sim_positions WHERE user_id = ?", (user_id,)
            ).fetchall()
        return [self._row_to_position(r) for r in rows]

    async def list_positions(self, user_id: int) -> list[dict]:
        return await asyncio.to_thread(self._list_positions_sync, user_id)

    def _get_position_sync(self, user_id: int, symbol: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_POSITION_COLUMNS)} FROM sim_positions WHERE user_id = ? AND symbol = ?",
                (user_id, symbol),
            ).fetchone()
        return self._row_to_position(row) if row else None

    async def get_position(self, user_id: int, symbol: str) -> dict | None:
        return await asyncio.to_thread(self._get_position_sync, user_id, symbol)

    def _upsert_position_sync(self, user_id: int, position: dict) -> None:
        d = dict(position)
        d["exit_order_ids"] = json.dumps(d.get("exit_order_ids") or [])
        columns = ("user_id", *_POSITION_COLUMNS)
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f"{c} = excluded.{c}" for c in _POSITION_COLUMNS if c != "symbol")
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO sim_positions ({', '.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT(user_id, symbol) DO UPDATE SET {assignments}",
                (user_id, *(d[c] for c in _POSITION_COLUMNS)),
            )

    async def upsert_position(self, user_id: int, position: dict) -> None:
        await asyncio.to_thread(self._upsert_position_sync, user_id, position)

    def _delete_position_sync(self, user_id: int, symbol: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sim_positions WHERE user_id = ? AND symbol = ?", (user_id, symbol))

    async def delete_position(self, user_id: int, symbol: str) -> None:
        await asyncio.to_thread(self._delete_position_sync, user_id, symbol)

    # --- orders ---------------------------------------------------------

    def _insert_order_sync(self, user_id: int, order: dict) -> None:
        columns = ("user_id", *_ORDER_COLUMNS)
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO sim_orders ({', '.join(columns)}) VALUES ({placeholders})",
                (user_id, *(order.get(c) for c in _ORDER_COLUMNS)),
            )

    async def insert_order(self, user_id: int, order: dict) -> None:
        await asyncio.to_thread(self._insert_order_sync, user_id, order)

    def _get_order_sync(self, user_id: int, order_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders WHERE id = ? AND user_id = ?",
                (order_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    async def get_order(self, user_id: int, order_id: str) -> dict | None:
        return await asyncio.to_thread(self._get_order_sync, user_id, order_id)

    def _update_order_sync(self, user_id: int, order_id: str, fields: dict) -> None:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE sim_orders SET {assignments} WHERE id = ? AND user_id = ?",
                (*fields.values(), order_id, user_id),
            )

    async def update_order(self, user_id: int, order_id: str, **fields) -> None:
        await asyncio.to_thread(self._update_order_sync, user_id, order_id, fields)

    def _list_orders_sync(self, user_id: int, status: str) -> list[dict]:
        with self._connect() as conn:
            if status == "open":
                placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
                rows = conn.execute(
                    f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders "
                    f"WHERE user_id = ? AND status IN ({placeholders}) ORDER BY submitted_at DESC",
                    (user_id, *_OPEN_STATUSES),
                ).fetchall()
            elif status == "closed":
                placeholders = ", ".join("?" for _ in _CLOSED_STATUSES)
                rows = conn.execute(
                    f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders "
                    f"WHERE user_id = ? AND status IN ({placeholders}) ORDER BY submitted_at DESC",
                    (user_id, *_CLOSED_STATUSES),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders "
                    f"WHERE user_id = ? ORDER BY submitted_at DESC",
                    (user_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    async def list_orders(self, user_id: int, status: str = "open") -> list[dict]:
        return await asyncio.to_thread(self._list_orders_sync, user_id, status)

    def _working_orders_by_symbol_sync(self, user_id: int) -> dict[str, list[dict]]:
        with self._connect() as conn:
            placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
            rows = conn.execute(
                f"SELECT {', '.join(_ORDER_COLUMNS)} FROM sim_orders "
                f"WHERE user_id = ? AND status IN ({placeholders})",
                (user_id, *_OPEN_STATUSES),
            ).fetchall()
        by_symbol: dict[str, list[dict]] = {}
        for row in rows:
            d = dict(row)
            by_symbol.setdefault(d["symbol"], []).append(d)
        return by_symbol

    async def working_orders_by_symbol(self, user_id: int) -> dict[str, list[dict]]:
        """One user's working orders, grouped by symbol -- what a single
        SimBroker instance's check_fills/close_position operate on. See
        all_working_orders for the cross-user version the fill loop uses to
        decide which symbols to price."""
        return await asyncio.to_thread(self._working_orders_by_symbol_sync, user_id)

    def _all_working_orders_sync(self) -> dict[int, dict[str, list[dict]]]:
        """Every user's working orders, grouped user_id -> symbol -> orders
        -- drives the fill loop, which needs to serve every user off one
        shared batched price fetch rather than querying per user."""
        with self._connect() as conn:
            placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
            rows = conn.execute(
                f"SELECT user_id, {', '.join(_ORDER_COLUMNS)} FROM sim_orders WHERE status IN ({placeholders})",
                _OPEN_STATUSES,
            ).fetchall()
        by_user: dict[int, dict[str, list[dict]]] = {}
        for row in rows:
            d = dict(row)
            user_id = d.pop("user_id")
            by_user.setdefault(user_id, {}).setdefault(d["symbol"], []).append(d)
        return by_user

    async def all_working_orders(self) -> dict[int, dict[str, list[dict]]]:
        return await asyncio.to_thread(self._all_working_orders_sync)

    def _cancel_oco_siblings_sync(self, user_id: int, oco_group_id: str, exclude_order_id: str, now: str) -> None:
        with self._connect() as conn:
            placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
            conn.execute(
                f"UPDATE sim_orders SET status = 'canceled', canceled_at = ? "
                f"WHERE user_id = ? AND oco_group_id = ? AND id != ? AND status IN ({placeholders})",
                (now, user_id, oco_group_id, exclude_order_id, *_OPEN_STATUSES),
            )

    async def cancel_oco_siblings(self, user_id: int, oco_group_id: str, exclude_order_id: str, now: datetime) -> None:
        await asyncio.to_thread(
            self._cancel_oco_siblings_sync, user_id, oco_group_id, exclude_order_id, now.isoformat()
        )

    def _activate_children_sync(self, user_id: int, parent_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sim_orders SET status = 'new' WHERE user_id = ? AND parent_id = ? AND status = 'held'",
                (user_id, parent_id),
            )

    async def activate_children(self, user_id: int, parent_id: str) -> None:
        await asyncio.to_thread(self._activate_children_sync, user_id, parent_id)

    def _cancel_children_sync(self, user_id: int, parent_id: str, now: str) -> None:
        with self._connect() as conn:
            placeholders = ", ".join("?" for _ in _OPEN_STATUSES)
            conn.execute(
                f"UPDATE sim_orders SET status = 'canceled', canceled_at = ? "
                f"WHERE user_id = ? AND parent_id = ? AND status IN ({placeholders})",
                (now, user_id, parent_id, *_OPEN_STATUSES),
            )

    async def cancel_children(self, user_id: int, parent_id: str, now: datetime) -> None:
        await asyncio.to_thread(self._cancel_children_sync, user_id, parent_id, now.isoformat())

    def _child_stop_price_sync(self, user_id: int, parent_id: str) -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT stop_price FROM sim_orders WHERE user_id = ? AND parent_id = ? AND leg_role = 'stop_loss'",
                (user_id, parent_id),
            ).fetchone()
        return float(row["stop_price"]) if row and row["stop_price"] is not None else None

    async def child_stop_price(self, user_id: int, parent_id: str) -> float | None:
        return await asyncio.to_thread(self._child_stop_price_sync, user_id, parent_id)

    # --- trades ---------------------------------------------------------

    def _insert_trade_sync(self, user_id: int, trade: dict, now: str) -> None:
        d = dict(trade)
        d["exit_order_ids"] = json.dumps(d.get("exit_order_ids") or [])
        d["multiplier"] = int(d.get("multiplier") or 1)
        columns = ("user_id", *_TRADE_COLUMNS)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO sim_trades ({', '.join(columns)}, recorded_at) "
                f"VALUES ({', '.join('?' for _ in columns)}, ?)",
                (user_id, *(d[c] for c in _TRADE_COLUMNS), now),
            )

    async def insert_trade(self, user_id: int, trade: dict) -> None:
        await asyncio.to_thread(self._insert_trade_sync, user_id, trade, datetime.now(UTC).isoformat())

    def _list_trades_sync(self, user_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_TRADE_COLUMNS)} FROM sim_trades WHERE user_id = ? "
                f"ORDER BY closed_at DESC, id",
                (user_id,),
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

    async def list_trades(self, user_id: int) -> list[dict]:
        return await asyncio.to_thread(self._list_trades_sync, user_id)
