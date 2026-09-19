"""Per-user interface preferences: the settings dialog's object and the
dashboard layout, keyed exactly as the browser keys them.

Same file/conventions as every other store here (stdlib sqlite3, fresh
connection per call via asyncio.to_thread, WAL mode). The reasoning is the
watchlist's (see app.watchlist.store): these lived in localStorage from
before real logins existed, which made them per-browser rather than
per-person -- two people sharing a browser shared a colour scheme and a
layout, and the same person on a second machine started from scratch.

A key/value table rather than columns: the settings object grows a field
whenever the interface does, and a migration per checkbox is not worth it.
The value is the JSON the browser already stores, kept opaque here -- the
frontend owns its shape and its defaults.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""


class UserPrefsStore:
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

    def _all_sync(self, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM user_prefs WHERE user_id = ?", (user_id,)
            ).fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                # A row written by an older shape, or by hand. Skipping it
                # lets the browser fall back to its own copy rather than
                # failing the whole fetch over one bad key.
                continue
        return out

    async def all(self, user_id: int) -> dict[str, Any]:
        return await asyncio.to_thread(self._all_sync, user_id)

    def _put_sync(self, user_id: int, key: str, value: str, now: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_prefs (user_id, key, value, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (user_id, key, value, now),
            )

    async def put(self, user_id: int, key: str, value: Any) -> None:
        await asyncio.to_thread(
            self._put_sync, user_id, key, json.dumps(value, separators=(",", ":")), datetime.now(UTC).isoformat()
        )

    def _delete_sync(self, user_id: int, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM user_prefs WHERE user_id = ? AND key = ?", (user_id, key))

    async def delete(self, user_id: int, key: str) -> None:
        await asyncio.to_thread(self._delete_sync, user_id, key)
