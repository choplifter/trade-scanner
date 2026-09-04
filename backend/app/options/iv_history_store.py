"""One ATM implied-vol reading per symbol per trading day, so that an IV
rank can eventually exist.

An IV rank is today's implied vol placed in its own trailing range, and
nothing in a live chain snapshot contains that range -- it has to be
accumulated. This store is the accumulation: one row per symbol per session,
written opportunistically whenever a chain is fetched for that symbol
anyway, so history builds for exactly the names actually being looked at
and costs one insert a day.

**A real IV rank is therefore not available on day one, and this module
says so rather than inventing one.** Below MIN_SAMPLES sessions `rank()`
returns None, and the caller reports the sample count instead. That is the
same rule the rest of the app follows for missing data: absent is absent,
never a signal. A "rank" computed from four days of history would look
authoritative and mean nothing.

Same sqlite file and conventions as the other stores (app.options
.trigger_store, app.scanners.history_store): sqlite3 on a worker thread,
WAL, schema created on startup.
"""

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime

logger = logging.getLogger(__name__)

# Roughly a trading month. Below this the highest and lowest readings seen
# are a coincidence of the sample rather than a range, and a percentage
# against them would be noise wearing a number's clothes.
MIN_SAMPLES = 20
# A year of sessions, the conventional IV-rank lookback.
LOOKBACK_SESSIONS = 252

_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_iv_history (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    atm_iv REAL NOT NULL,
    dte INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (symbol, session_date)
);
"""


@dataclass(frozen=True)
class IvRank:
    """Where today's ATM IV sits in the range seen so far."""

    percent: float
    samples: int
    low: float
    high: float

    def to_dict(self) -> dict:
        return {
            "percent": round(self.percent, 1),
            "samples": self.samples,
            "low": round(self.low, 4),
            "high": round(self.high, 4),
        }


def rank_within(current: float, history: list[float], *, min_samples: int = MIN_SAMPLES) -> IvRank | None:
    """Pure: today's reading against a list of past ones. None below
    `min_samples`, and None when every reading is identical -- a flat range
    has no position within it to report."""
    if current is None or len(history) < min_samples:
        return None
    low, high = min(history), max(history)
    if high <= low:
        return None
    # Clamped: today's reading can sit outside the range it is measured
    # against (a fresh high is exactly when this matters), and "112% of
    # range" is a worse way to say "at the top of it".
    percent = max(0.0, min(100.0, (current - low) / (high - low) * 100))
    return IvRank(percent=percent, samples=len(history), low=low, high=high)


class IvHistoryStore:
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

    def _record_sync(self, symbol: str, session_date: date, atm_iv: float, dte: int) -> None:
        with self._connect() as conn:
            # Last write of the day wins. A reading taken near the close is
            # the more representative one, and the alternative -- keeping the
            # first -- would pin the series to the open.
            conn.execute(
                "INSERT INTO option_iv_history (symbol, session_date, atm_iv, dte, recorded_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol, session_date) DO UPDATE SET "
                "atm_iv = excluded.atm_iv, dte = excluded.dte, recorded_at = excluded.recorded_at",
                (symbol.upper(), session_date.isoformat(), atm_iv, dte, datetime.now(UTC).isoformat()),
            )

    async def record(self, symbol: str, session_date: date, atm_iv: float, dte: int) -> None:
        """Best-effort by design: this is a side-effect of serving something
        else, and a failed insert must never take that down."""
        if not atm_iv or atm_iv <= 0:
            return
        try:
            await asyncio.to_thread(self._record_sync, symbol, session_date, atm_iv, dte)
        except Exception:
            logger.exception("Failed to record ATM IV for %s", symbol)

    def _history_sync(self, symbol: str, limit: int) -> list[float]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT atm_iv FROM option_iv_history WHERE symbol = ? "
                "ORDER BY session_date DESC LIMIT ?",
                (symbol.upper(), limit),
            ).fetchall()
        return [row["atm_iv"] for row in rows]

    async def history(self, symbol: str, limit: int = LOOKBACK_SESSIONS) -> list[float]:
        try:
            return await asyncio.to_thread(self._history_sync, symbol, limit)
        except Exception:
            logger.exception("Failed to read ATM IV history for %s", symbol)
            return []

    async def rank(self, symbol: str, current: float | None) -> tuple[IvRank | None, int]:
        """(rank, samples). The sample count comes back either way so a
        caller can say "no rank yet, 7 sessions recorded" instead of just
        going quiet -- the difference between "IV is not elevated" and "we
        do not know yet"."""
        if current is None:
            return None, 0
        history = await self.history(symbol)
        return rank_within(current, history), len(history)
