"""Persistent state for history-replay sessions -- same file, same
conventions as app.trading.sim.store.SimStore (stdlib sqlite3, a fresh
connection per call via asyncio.to_thread, WAL mode).

Only session metadata lives here: which symbols/date range a user is
replaying and where their clock currently sits (as_of, playing, speed).
The bars themselves are never persisted -- see app.replay.engine's module
docstring -- so a server restart loses nothing durable, just the resident
ReplayEngine, which load_replay_engine() rebuilds from this row plus the
same disk-cached bar fetch every other backtest tool uses.

One active session per user (replay_sessions.user_id is the PK), same
"per-user, not global" scoping SimStore established -- a second
POST /api/replay/start simply overwrites the caller's own row.
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS replay_sessions (
    user_id INTEGER PRIMARY KEY,
    symbols TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    lookback_days INTEGER NOT NULL,
    as_of TEXT NOT NULL,
    playing INTEGER NOT NULL DEFAULT 0,
    speed REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _row_to_session(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["symbols"] = json.loads(d["symbols"])
    except (TypeError, ValueError):
        d["symbols"] = []
    d["playing"] = bool(d["playing"])
    return d


class ReplayStore:
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

    # --- session lifecycle -----------------------------------------------

    def _start_sync(
        self,
        user_id: int,
        symbols: list[str],
        start_date: str,
        end_date: str,
        lookback_days: int,
        as_of: str,
        speed: float,
        now: str,
    ) -> dict:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO replay_sessions "
                "(user_id, symbols, start_date, end_date, lookback_days, as_of, playing, speed, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "symbols = excluded.symbols, start_date = excluded.start_date, "
                "end_date = excluded.end_date, lookback_days = excluded.lookback_days, "
                "as_of = excluded.as_of, playing = 0, speed = excluded.speed, "
                "updated_at = excluded.updated_at",
                (user_id, json.dumps(symbols), start_date, end_date, lookback_days, as_of, speed, now, now),
            )
            row = conn.execute("SELECT * FROM replay_sessions WHERE user_id = ?", (user_id,)).fetchone()
        return _row_to_session(row)

    async def start(
        self, user_id: int, symbols: list[str], start_date: str, end_date: str, lookback_days: int, as_of: str, speed: float = 1.0
    ) -> dict:
        return await asyncio.to_thread(
            self._start_sync, user_id, symbols, start_date, end_date, lookback_days, as_of, speed,
            datetime.now(UTC).isoformat(),
        )

    def _get_sync(self, user_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM replay_sessions WHERE user_id = ?", (user_id,)).fetchone()
        return _row_to_session(row) if row else None

    async def get(self, user_id: int) -> dict | None:
        return await asyncio.to_thread(self._get_sync, user_id)

    def _update_sync(self, user_id: int, fields: dict, now: str) -> dict | None:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE replay_sessions SET {assignments}, updated_at = ? WHERE user_id = ?",
                (*fields.values(), now, user_id),
            )
            row = conn.execute("SELECT * FROM replay_sessions WHERE user_id = ?", (user_id,)).fetchone()
        return _row_to_session(row) if row else None

    async def update(self, user_id: int, **fields) -> dict | None:
        """Partial update (as_of/playing/speed) -- what play/pause/seek/speed
        endpoints call. Returns None if the user has no session (start was
        never called), which the router turns into a 404."""
        return await asyncio.to_thread(self._update_sync, user_id, fields, datetime.now(UTC).isoformat())

    def _all_playing_sync(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM replay_sessions WHERE playing = 1").fetchall()
        return [_row_to_session(r) for r in rows]

    async def all_playing(self) -> list[dict]:
        """Every user currently in play state -- drives the pacing loop,
        the same way SimStore.all_working_orders drives the fill loop."""
        return await asyncio.to_thread(self._all_playing_sync)

    def _all_sessions_sync(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM replay_sessions").fetchall()
        return [_row_to_session(r) for r in rows]

    async def all_sessions(self) -> list[dict]:
        """Every session, playing or paused -- the live sim fill loop skips
        these users, whose orders price against the replayed moment (see
        routers/trading_sim.py's _replay_seam for why paused counts)."""
        return await asyncio.to_thread(self._all_sessions_sync)

    def _stop_sync(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM replay_sessions WHERE user_id = ?", (user_id,))

    async def stop(self, user_id: int) -> None:
        await asyncio.to_thread(self._stop_sync, user_id)
