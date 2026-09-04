"""Server-wide switches that survive a restart -- a key/value table in the
same sqlite file as everything else (the scanner pause, for one). Same
conventions as the other stores: stdlib sqlite3, a fresh connection per
call via asyncio.to_thread, WAL mode."""

import asyncio
import sqlite3
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class KeyValueStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    def _get_sync(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM app_kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    async def get(self, key: str, default: str | None = None) -> str | None:
        value = await asyncio.to_thread(self._get_sync, key)
        return default if value is None else value

    async def get_bool(self, key: str, default: bool = False) -> bool:
        value = await self.get(key)
        if value is None:
            return default
        return value.strip().lower() in ("1", "true", "yes", "on")

    def _set_sync(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app_kv (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, datetime.now(UTC).isoformat()),
            )

    async def set(self, key: str, value: str | bool) -> None:
        await asyncio.to_thread(self._set_sync, key, "1" if value is True else "0" if value is False else str(value))
