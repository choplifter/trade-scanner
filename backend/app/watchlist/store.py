"""Per-user watchlist symbol lists. Same file/conventions as every other
store here (stdlib sqlite3, fresh connection per call via asyncio.to_thread,
WAL mode). Was localStorage-only (per-browser, not per-person) before real
users existed -- now follows the logged-in user instead.
"""

import asyncio
import sqlite3
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist_symbols (
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, symbol)
);
"""


class WatchlistStore:
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

    def _list_symbols_sync(self, user_id: int) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol FROM watchlist_symbols WHERE user_id = ? ORDER BY added_at", (user_id,)
            ).fetchall()
        return [r["symbol"] for r in rows]

    async def list_symbols(self, user_id: int) -> list[str]:
        return await asyncio.to_thread(self._list_symbols_sync, user_id)

    def _add_symbol_sync(self, user_id: int, symbol: str, now: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist_symbols (user_id, symbol, added_at) VALUES (?, ?, ?)",
                (user_id, symbol, now),
            )

    async def add_symbol(self, user_id: int, symbol: str) -> None:
        await asyncio.to_thread(self._add_symbol_sync, user_id, symbol, datetime.now(UTC).isoformat())

    def _remove_symbol_sync(self, user_id: int, symbol: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM watchlist_symbols WHERE user_id = ? AND symbol = ?", (user_id, symbol))

    async def remove_symbol(self, user_id: int, symbol: str) -> None:
        await asyncio.to_thread(self._remove_symbol_sync, user_id, symbol)

    def _seed_if_empty_sync(self, user_id: int, defaults: list[str], now: str) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM watchlist_symbols WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
            if existing is None:
                conn.executemany(
                    "INSERT OR IGNORE INTO watchlist_symbols (user_id, symbol, added_at) VALUES (?, ?, ?)",
                    [(user_id, s, now) for s in defaults],
                )

    async def seed_if_empty(self, user_id: int, defaults: list[str]) -> None:
        """A fresh user still gets the symbols_pinned.txt-derived default
        list on first use -- checked/inserted every call (cheap, a single
        indexed SELECT) rather than tracked with a separate "have I seeded
        this user" flag, so it's correct even if a user's list is emptied
        out entirely by hand later (still counts as "has rows", stays empty)
        versus never having been touched at all."""
        await asyncio.to_thread(self._seed_if_empty_sync, user_id, defaults, datetime.now(UTC).isoformat())
