"""Campaigns and their events -- the persistent half of a playbook.

A campaign is one playbook running on one symbol in one account for one
user, with its parameters, its status and a cached snapshot of the numbers
the list shows. Nothing about positions is stored: the runner rebuilds the
snapshot from the books every tick, and the events table is the only
history -- what was sold, assigned, rolled, called away, and for how much.

Same sqlite file and conventions as app.options.trigger_store (WAL,
_ADDED_COLUMNS migrations, asyncio.to_thread around every query). One live
campaign per (user, account, symbol): a partial unique index, so a closed
campaign leaves room for the next one on the same symbol.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS playbook_campaigns (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    account TEXT NOT NULL,
    symbol TEXT NOT NULL,
    playbook TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'cash',
    auto_execute INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    shares INTEGER NOT NULL DEFAULT 0,
    shares_avg_entry REAL,
    cost_basis REAL,
    premiums_collected REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    proposal TEXT,
    proposal_at TEXT,
    proposal_error TEXT,
    orders_cursor TEXT,
    executed_order_id TEXT,
    last_error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_playbook_campaigns_live
    ON playbook_campaigns(user_id, account, symbol) WHERE status != 'closed';
CREATE INDEX IF NOT EXISTS idx_playbook_campaigns_user
    ON playbook_campaigns(user_id, account, status);
CREATE TABLE IF NOT EXISTS playbook_events (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    occ TEXT,
    qty INTEGER NOT NULL DEFAULT 0,
    price REAL,
    cash_delta REAL,
    order_id TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_playbook_events_campaign
    ON playbook_events(campaign_id, at);
"""

_CAMPAIGN_COLUMNS = (
    "id", "user_id", "account", "symbol", "playbook", "params", "status", "phase", "auto_execute",
    "created_at", "updated_at", "closed_at", "shares", "shares_avg_entry", "cost_basis",
    "premiums_collected", "realized_pnl", "proposal", "proposal_at", "proposal_error",
    "orders_cursor", "executed_order_id", "last_error",
)
_EVENT_COLUMNS = ("id", "campaign_id", "user_id", "at", "kind", "occ", "qty", "price", "cash_delta", "order_id", "note")

# Columns added after the table first shipped (none yet); kept for the
# migration pattern every store here follows.
_ADDED_COLUMNS: dict[str, str] = {}

ACTIVE = "active"
PAUSED = "paused"
CLOSED = "closed"
STATUSES = (ACTIVE, PAUSED, CLOSED)

# Event kinds. Option events carry cash_delta (dollars, signed: a credit
# positive); share events carry qty (shares) and price (the strike).
# bought_call / sold_long are the long side (a poor man's wheel's LEAPS
# bought and sold again), kept apart so "premiums" stays the short side.
EVENT_KINDS = (
    "started", "sold_put", "sold_call", "closed", "rolled", "expired", "cash_settled", "assigned", "called_away",
    "bought_call", "sold_long",
    "shares_changed", "manual_note", "paused", "resumed", "campaign_closed", "executed", "execute_failed",
)


def _campaign_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("params", "proposal"):
        raw = d.get(key)
        if raw:
            try:
                d[key] = json.loads(raw)
            except (TypeError, ValueError):
                d[key] = None
        else:
            d[key] = {} if key == "params" else None
    d["auto_execute"] = bool(d.get("auto_execute"))
    return d


class PlaybookStore:
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
            present = {row[1] for row in conn.execute("PRAGMA table_info(playbook_campaigns)")}
            for column, kind in _ADDED_COLUMNS.items():
                if column not in present:
                    conn.execute(f"ALTER TABLE playbook_campaigns ADD COLUMN {column} {kind}")

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    # --- campaigns -----------------------------------------------------------

    def _create_sync(self, user_id: int, account: str, symbol: str, playbook: str, params: dict, auto_execute: bool, now: datetime) -> dict:
        campaign_id = uuid.uuid4().hex
        stamp = now.isoformat()
        with self._connect() as conn:
            try:
                conn.execute(
                    f"INSERT INTO playbook_campaigns ({', '.join(_CAMPAIGN_COLUMNS)}) VALUES ({', '.join('?' for _ in _CAMPAIGN_COLUMNS)})",
                    # orders_cursor starts at the creation stamp: orders filled
                    # before the campaign existed are not its premiums.
                    (
                        campaign_id, user_id, account, symbol.upper(), playbook, json.dumps(params), ACTIVE, "cash",
                        1 if auto_execute else 0, stamp, stamp, None, 0, None, None, 0.0, 0.0, None, None, None, stamp, None, None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"A campaign on {symbol.upper()} is already running in the {account} account.") from exc
            row = conn.execute(f"SELECT {', '.join(_CAMPAIGN_COLUMNS)} FROM playbook_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return _campaign_row(row)

    async def create(self, user_id: int, account: str, symbol: str, playbook: str, params: dict, *, auto_execute: bool = False, now: datetime | None = None) -> dict:
        return await asyncio.to_thread(self._create_sync, user_id, account, symbol, playbook, params, auto_execute, now or datetime.now(UTC))

    def _get_sync(self, campaign_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {', '.join(_CAMPAIGN_COLUMNS)} FROM playbook_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return _campaign_row(row) if row else None

    async def get(self, campaign_id: str) -> dict | None:
        return await asyncio.to_thread(self._get_sync, campaign_id)

    def _list_sync(self, user_id: int, account: str | None, include_closed: bool) -> list[dict]:
        clauses = ["user_id = ?"]
        args: list = [user_id]
        if account is not None:
            clauses.append("account = ?")
            args.append(account)
        if not include_closed:
            clauses.append("status != ?")
            args.append(CLOSED)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_CAMPAIGN_COLUMNS)} FROM playbook_campaigns WHERE {' AND '.join(clauses)} ORDER BY created_at DESC",
                args,
            ).fetchall()
        return [_campaign_row(r) for r in rows]

    async def list_for_user(self, user_id: int, account: str | None = None, *, include_closed: bool = False) -> list[dict]:
        return await asyncio.to_thread(self._list_sync, user_id, account, include_closed)

    def _all_active_sync(self, account: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_CAMPAIGN_COLUMNS)} FROM playbook_campaigns WHERE account = ? AND status = ? ORDER BY created_at",
                (account, ACTIVE),
            ).fetchall()
        return [_campaign_row(r) for r in rows]

    async def all_active(self, account: str) -> list[dict]:
        """Every active campaign in `account`, across users -- who the loop
        has to look at."""
        return await asyncio.to_thread(self._all_active_sync, account)

    def _update_sync(self, campaign_id: str, fields: dict, now: datetime) -> dict | None:
        if not fields:
            return self._get_sync(campaign_id)
        cols = []
        args: list = []
        for key, value in fields.items():
            if key not in _CAMPAIGN_COLUMNS or key in ("id", "user_id", "created_at"):
                raise ValueError(f"cannot update {key}")
            if key in ("params", "proposal"):
                value = json.dumps(value) if value is not None else None
            if key == "auto_execute":
                value = 1 if value else 0
            cols.append(f"{key} = ?")
            args.append(value)
        cols.append("updated_at = ?")
        args.append(now.isoformat())
        args.append(campaign_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE playbook_campaigns SET {', '.join(cols)} WHERE id = ?", args)
        return self._get_sync(campaign_id)

    async def update(self, campaign_id: str, *, now: datetime | None = None, **fields) -> dict | None:
        return await asyncio.to_thread(self._update_sync, campaign_id, fields, now or datetime.now(UTC))

    async def set_status(self, campaign_id: str, status: str, *, now: datetime | None = None) -> dict | None:
        if status not in STATUSES:
            raise ValueError(f"not a status: {status}")
        fields: dict = {"status": status}
        if status == CLOSED:
            fields["closed_at"] = (now or datetime.now(UTC)).isoformat()
        return await self.update(campaign_id, now=now, **fields)

    # --- events --------------------------------------------------------------

    def _add_event_sync(self, campaign_id: str, user_id: int, at: datetime, kind: str, occ: str | None, qty: int, price: float | None, cash_delta: float | None, order_id: str | None, note: str | None) -> dict:
        event_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO playbook_events ({', '.join(_EVENT_COLUMNS)}) VALUES ({', '.join('?' for _ in _EVENT_COLUMNS)})",
                (event_id, campaign_id, user_id, at.isoformat(), kind, occ, int(qty), price, cash_delta, order_id, note),
            )
            row = conn.execute(f"SELECT {', '.join(_EVENT_COLUMNS)} FROM playbook_events WHERE id = ?", (event_id,)).fetchone()
        return dict(row)

    async def add_event(
        self,
        campaign_id: str,
        user_id: int,
        kind: str,
        *,
        at: datetime | None = None,
        occ: str | None = None,
        qty: int = 0,
        price: float | None = None,
        cash_delta: float | None = None,
        order_id: str | None = None,
        note: str | None = None,
    ) -> dict:
        if kind not in EVENT_KINDS:
            raise ValueError(f"not an event kind: {kind}")
        return await asyncio.to_thread(
            self._add_event_sync, campaign_id, user_id, at or datetime.now(UTC), kind, occ, qty, price, cash_delta, order_id, note
        )

    def _events_sync(self, campaign_id: str, limit: int | None) -> list[dict]:
        with self._connect() as conn:
            sql = f"SELECT {', '.join(_EVENT_COLUMNS)} FROM playbook_events WHERE campaign_id = ? ORDER BY at, rowid"
            rows = conn.execute(sql, (campaign_id,)).fetchall()
        out = [dict(r) for r in rows]
        return out[-limit:] if limit else out

    async def events(self, campaign_id: str, *, limit: int | None = None) -> list[dict]:
        return await asyncio.to_thread(self._events_sync, campaign_id, limit)

    def _has_order_event_sync(self, campaign_id: str, order_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM playbook_events WHERE campaign_id = ? AND order_id = ? LIMIT 1", (campaign_id, order_id)).fetchone()
        return row is not None

    async def has_order_event(self, campaign_id: str, order_id: str) -> bool:
        return await asyncio.to_thread(self._has_order_event_sync, campaign_id, order_id)
