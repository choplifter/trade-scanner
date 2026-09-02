"""Per-user notes on closed trades. Same file/conventions as every other store
here (stdlib sqlite3, fresh connection per call via asyncio.to_thread, WAL
mode) -- see app.watchlist.store.WatchlistStore, which this mirrors.

trade_id is opaque here: it's either a real Trade.id ("SYMBOL:entry_order_id:
leg", see app.trading.trades.Trade) or a Simulation Mode trade's uuid (see
app.trading.sim.store.SimStore) -- both are globally unique on their own, so
this store doesn't need to know or care which one it's annotating.
"""

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_journal_entries (
    user_id INTEGER NOT NULL,
    trade_id TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    rating INTEGER,
    tags TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, trade_id)
);
"""


@dataclass
class JournalEntry:
    trade_id: str
    note: str
    rating: int | None
    tags: list[str]
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "note": self.note,
            "rating": self.rating,
            "tags": self.tags,
            "updated_at": self.updated_at,
        }


def _row_to_entry(row: sqlite3.Row) -> JournalEntry:
    return JournalEntry(
        trade_id=row["trade_id"],
        note=row["note"],
        rating=row["rating"],
        tags=json.loads(row["tags"]),
        updated_at=row["updated_at"],
    )


class JournalStore:
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

    def _list_entries_sync(self, user_id: int) -> dict[str, JournalEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT trade_id, note, rating, tags, updated_at FROM trade_journal_entries WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {row["trade_id"]: _row_to_entry(row) for row in rows}

    async def list_entries(self, user_id: int) -> dict[str, JournalEntry]:
        return await asyncio.to_thread(self._list_entries_sync, user_id)

    def _upsert_entry_sync(
        self, user_id: int, trade_id: str, note: str, rating: int | None, tags: list[str], now: str
    ) -> JournalEntry:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_journal_entries (user_id, trade_id, note, rating, tags, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, trade_id) DO UPDATE SET
                    note = excluded.note,
                    rating = excluded.rating,
                    tags = excluded.tags,
                    updated_at = excluded.updated_at
                """,
                (user_id, trade_id, note, rating, json.dumps(tags), now),
            )
        return JournalEntry(trade_id=trade_id, note=note, rating=rating, tags=tags, updated_at=now)

    async def upsert_entry(
        self, user_id: int, trade_id: str, note: str, rating: int | None, tags: list[str]
    ) -> JournalEntry:
        return await asyncio.to_thread(
            self._upsert_entry_sync, user_id, trade_id, note, rating, tags, datetime.now(UTC).isoformat()
        )
