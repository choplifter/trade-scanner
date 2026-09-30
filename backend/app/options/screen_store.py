"""The last screen per strategy, kept so the tab opens on a table instead
of on a spinner.

A universe screen is forty chain fetches and a dozen seconds -- fine to
ask for, wrong to make someone wait for every time they open a tab. The
background pass (app.options.screen_job) writes its answer here and the
tab reads it, with the run's own timestamp so a stale table says so
rather than pretending to be live.

One row per strategy, replaced in place: the point is "what does the
market look like now", not a history. The payload is the response the
endpoint would have returned, stored whole -- it is a few tens of
kilobytes, and keeping it intact means the reader of a stored run and the
reader of a live one are looking at the same shape.

Same file and conventions as the other stores (sqlite3 on a worker
thread, WAL, schema created at startup).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_screen_runs (
    strategy TEXT PRIMARY KEY,
    ran_at TEXT NOT NULL,
    rows_count INTEGER NOT NULL,
    payload TEXT NOT NULL
);
"""


class ScreenStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    def _save_sync(self, strategy: str, ran_at: str, rows_count: int, payload: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO option_screen_runs (strategy, ran_at, rows_count, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(strategy) DO UPDATE SET ran_at = excluded.ran_at, "
                "rows_count = excluded.rows_count, payload = excluded.payload",
                (strategy, ran_at, rows_count, payload),
            )

    async def save(self, strategy: str, body: dict) -> None:
        """Best-effort: a screen nobody can store is still a screen the
        caller just read, so a failed write must not take the pass down."""
        try:
            await asyncio.to_thread(
                self._save_sync,
                strategy,
                datetime.now(UTC).isoformat(timespec="seconds"),
                len(body.get("rows") or []),
                json.dumps(body),
            )
        except Exception:
            logger.exception("Failed to store the %s screen", strategy)

    def _latest_sync(self, strategy: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ran_at, rows_count, payload FROM option_screen_runs WHERE strategy = ?", (strategy,)
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            return None
        # The age is what makes a stored run honest: the reader has to be
        # able to tell a table from four minutes ago from one from Friday.
        payload["stored_at"] = row["ran_at"]
        return payload

    async def latest(self, strategy: str) -> dict | None:
        try:
            return await asyncio.to_thread(self._latest_sync, strategy)
        except Exception:
            logger.exception("Failed to read the stored %s screen", strategy)
            return None

    def _strategies_sync(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT strategy, ran_at, rows_count FROM option_screen_runs ORDER BY strategy"
            ).fetchall()
        return [dict(row) for row in rows]

    async def strategies(self) -> list[dict]:
        """What is stored and when it was run -- enough for a tab to say
        "credit spread, 6 minutes ago" without loading the payload."""
        try:
            return await asyncio.to_thread(self._strategies_sync)
        except Exception:
            logger.exception("Failed to list stored screens")
            return []
