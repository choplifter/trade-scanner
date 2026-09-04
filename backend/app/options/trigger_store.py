"""Exit triggers for open spreads and contracts: "close this if SPY
trades below 740 / above 775", and/or "close this if its own premium
(mark) falls below 1.20 / rises above 4.00". Alpaca takes no stop orders
on options, so the dashboard keeps these itself and app.options.monitor
watches them.

Persisted (same sqlite file and conventions as app.trading.journal_store)
so a backend restart does not silently drop a stop someone is relying on.
Per user *and* per account: two logins on the shared paper account may
arm their own triggers, and a live trigger never fires a paper close.
"""

import asyncio
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from app.options.models import TriggerCreate

_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_underlying_triggers (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    account TEXT NOT NULL DEFAULT 'paper',
    underlying TEXT NOT NULL,
    expiry TEXT NOT NULL,
    legs TEXT NOT NULL,
    qty INTEGER NOT NULL,
    close_below REAL,
    close_above REAL,
    premium_below REAL,
    premium_above REAL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fired_at TEXT,
    fired_price REAL,
    fired_order_id TEXT,
    fired_on TEXT
);
CREATE INDEX IF NOT EXISTS idx_option_triggers_user_status
    ON option_underlying_triggers(user_id, status);
"""

_COLUMNS = (
    "id", "user_id", "account", "underlying", "expiry", "legs", "qty", "close_below",
    "close_above", "premium_below", "premium_above", "status", "attempts", "last_error",
    "created_at", "updated_at", "fired_at", "fired_price", "fired_order_id", "fired_on",
)

# Columns added after the table first shipped; init_schema adds any that an
# existing database lacks (same PRAGMA table_info + ALTER pattern as
# app.trading.trade_store).
_ADDED_COLUMNS = {
    "premium_below": "REAL",
    "premium_above": "REAL",
    "fired_on": "TEXT",
}

ACTIVE = "active"
FIRED = "fired"
CANCELLED = "cancelled"
FAILED = "failed"
ORPHANED = "orphaned"


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["legs"] = json.loads(d["legs"])
    except (TypeError, ValueError):
        d["legs"] = []
    return d


class TriggerStore:
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
            present = {row[1] for row in conn.execute("PRAGMA table_info(option_underlying_triggers)")}
            for column, kind in _ADDED_COLUMNS.items():
                if column not in present:
                    conn.execute(f"ALTER TABLE option_underlying_triggers ADD COLUMN {column} {kind}")

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    # --- writes -------------------------------------------------------------

    def _create_sync(self, user_id: int, account: str, body: TriggerCreate, now: datetime) -> dict:
        trigger_id = uuid.uuid4().hex
        stamp = now.isoformat()
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO option_underlying_triggers ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                (
                    trigger_id, user_id, account, body.underlying.upper(), body.expiry.isoformat(),
                    json.dumps([{"symbol": leg.symbol, "qty": leg.qty} for leg in body.legs]),
                    body.qty, body.close_below, body.close_above, body.premium_below, body.premium_above,
                    ACTIVE, 0, None, stamp, stamp, None, None, None, None,
                ),
            )
            row = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM option_underlying_triggers WHERE id = ?", (trigger_id,)
            ).fetchone()
        return _row_to_dict(row)

    async def create(self, user_id: int, account: str, body: TriggerCreate, now: datetime | None = None) -> dict:
        return await asyncio.to_thread(self._create_sync, user_id, account, body, now or datetime.now(UTC))

    def _cancel_sync(self, user_id: int, trigger_id: str, now: datetime) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE option_underlying_triggers SET status = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ? AND status = ?",
                (CANCELLED, now.isoformat(), trigger_id, user_id, ACTIVE),
            )
            return cur.rowcount > 0

    async def cancel(self, user_id: int, trigger_id: str, now: datetime | None = None) -> bool:
        """True if an active trigger of this user was cancelled; False for
        anyone else's, or one that already fired/failed/was cancelled."""
        return await asyncio.to_thread(self._cancel_sync, user_id, trigger_id, now or datetime.now(UTC))

    def _mark_sync(self, trigger_id: str, fields: dict) -> None:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE option_underlying_triggers SET {assignments} WHERE id = ?",
                (*fields.values(), trigger_id),
            )

    async def mark_fired(
        self,
        trigger_id: str,
        price: float,
        order_id: str | None,
        now: datetime | None = None,
        *,
        on: str = "underlying",
    ) -> None:
        """`price` is the value that crossed the bound -- the underlying's
        last, or the premium when `on` is "premium"."""
        stamp = (now or datetime.now(UTC)).isoformat()
        await asyncio.to_thread(
            self._mark_sync,
            trigger_id,
            {
                "status": FIRED,
                "fired_at": stamp,
                "fired_price": price,
                "fired_order_id": order_id,
                "fired_on": on,
                "updated_at": stamp,
            },
        )

    async def mark_failed(
        self, trigger_id: str, error: str, attempts: int, *, final: bool, now: datetime | None = None
    ) -> None:
        """Record an attempt that did not get a closing order out. Stays
        active (to be retried next tick) until `final`."""
        stamp = (now or datetime.now(UTC)).isoformat()
        fields = {"attempts": attempts, "last_error": error[:500], "updated_at": stamp}
        if final:
            fields["status"] = FAILED
        await asyncio.to_thread(self._mark_sync, trigger_id, fields)

    async def mark_orphaned(self, trigger_id: str, reason: str, now: datetime | None = None) -> None:
        stamp = (now or datetime.now(UTC)).isoformat()
        await asyncio.to_thread(
            self._mark_sync, trigger_id, {"status": ORPHANED, "last_error": reason[:500], "updated_at": stamp}
        )

    # --- reads --------------------------------------------------------------

    def _list_for_user_sync(self, user_id: int, account: str, since: datetime) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM option_underlying_triggers "
                "WHERE user_id = ? AND account = ? AND (status = ? OR updated_at >= ?) "
                "ORDER BY created_at DESC",
                (user_id, account, ACTIVE, since.isoformat()),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    async def list_for_user(
        self, user_id: int, account: str = "paper", include_finished_hours: float = 24.0, now: datetime | None = None
    ) -> list[dict]:
        """The user's active triggers on this account plus the ones that
        finished (fired, failed, orphaned, cancelled) recently, so a fired
        stop is still visible with its order id."""
        since = (now or datetime.now(UTC)) - timedelta(hours=include_finished_hours)
        return await asyncio.to_thread(self._list_for_user_sync, user_id, account, since)

    def _all_active_sync(self, account: str | None) -> list[dict]:
        with self._connect() as conn:
            if account is None:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM option_underlying_triggers WHERE status = ? ORDER BY created_at",
                    (ACTIVE,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM option_underlying_triggers "
                    "WHERE status = ? AND account = ? ORDER BY created_at",
                    (ACTIVE, account),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    async def all_active(self, account: str | None = None) -> list[dict]:
        """Every armed trigger across users (and accounts, unless one is
        named) -- the monitor's input."""
        return await asyncio.to_thread(self._all_active_sync, account)
