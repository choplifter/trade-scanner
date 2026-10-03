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
# The stretch of the curve a reading has to come from to be comparable
# with the others. A rank is today's implied volatility against this
# symbol's own past ones, and an expiry one day out is not the same
# measurement as one fifty days out: on the day MU reported, the 1-DTE
# chain priced 178 % against the 51-DTE chain's 57 %. Both are real and
# putting them in one history makes the rank meaningless.
#
# Readings outside the band are neither recorded nor read -- the filter on
# the read side is what makes the rows already written before this existed
# harmless, without a migration.
COMPARABLE_DTE = (20, 90)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_iv_history (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    atm_iv REAL NOT NULL,
    dte INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'recorder',
    PRIMARY KEY (symbol, session_date)
);
"""

# Where a row came from. 'recorder' is our own ATM reading (the IV recorder,
# or a chain someone opened); 'barchart' a day seeded from Barchart's daily
# IV history (scripts/import_barchart_iv.py), so a rank exists without a
# year of waiting. Kept apart so a seeded history can be told from ours and
# removed again -- Barchart's IV is a 30-day figure, ours the ATM of a
# 30-60 day expiry, and on 67 shared days in Sept/Oct 2026 theirs read a
# median 0.976 of ours: close enough for a rank, not the same measurement.
SOURCE_RECORDER = "recorder"
SOURCE_BARCHART = "barchart"


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
            # Tables created before `source` existed: every row in them is
            # ours, which is what the default says.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(option_iv_history)")}
            if "source" not in columns:
                conn.execute(f"ALTER TABLE option_iv_history ADD COLUMN source TEXT NOT NULL DEFAULT '{SOURCE_RECORDER}'")

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    def _record_sync(self, symbol: str, session_date: date, atm_iv: float, dte: int) -> None:
        with self._connect() as conn:
            # Last write of the day wins. A reading taken near the close is
            # the more representative one, and the alternative -- keeping the
            # first -- would pin the series to the open. Our own reading
            # also replaces a seeded one for the same day.
            conn.execute(
                "INSERT INTO option_iv_history (symbol, session_date, atm_iv, dte, recorded_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol, session_date) DO UPDATE SET "
                "atm_iv = excluded.atm_iv, dte = excluded.dte, recorded_at = excluded.recorded_at, source = excluded.source",
                (symbol.upper(), session_date.isoformat(), atm_iv, dte, datetime.now(UTC).isoformat(), SOURCE_RECORDER),
            )

    def seed_sync(self, symbol: str, readings: list[tuple[date, float]], *, source: str, dte: int) -> int:
        """Fill days this symbol has no usable reading for, from another
        source. Our own reading for a day stays -- unless it came from
        outside the comparable band, which the rank never reads: a day
        someone opened a 1-DTE chain is a day with no reading, not one to
        keep a seed out of (on 2026-10-03 that was 25 of SPY's last 30
        sessions). Returns how many rows went in or were replaced.
        Synchronous -- a one-off import, not a request."""
        if not COMPARABLE_DTE[0] <= dte <= COMPARABLE_DTE[1]:
            raise ValueError(f"dte {dte} is outside the comparable band {COMPARABLE_DTE}")
        now = datetime.now(UTC).isoformat()
        rows = [(symbol.upper(), day.isoformat(), iv, dte, now, source) for day, iv in readings if iv and iv > 0]
        with self._connect() as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT INTO option_iv_history (symbol, session_date, atm_iv, dte, recorded_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol, session_date) DO UPDATE SET "
                "atm_iv = excluded.atm_iv, dte = excluded.dte, recorded_at = excluded.recorded_at, source = excluded.source "
                f"WHERE option_iv_history.dte NOT BETWEEN {COMPARABLE_DTE[0]} AND {COMPARABLE_DTE[1]}",
                rows,
            )
            return conn.total_changes - before

    async def record(self, symbol: str, session_date: date, atm_iv: float, dte: int) -> None:
        """Best-effort by design: this is a side-effect of serving something
        else, and a failed insert must never take that down."""
        if not atm_iv or atm_iv <= 0:
            return
        if not COMPARABLE_DTE[0] <= dte <= COMPARABLE_DTE[1]:
            # Not an error: the caller read a chain, it was simply the
            # wrong part of the curve to compare across days.
            return
        try:
            await asyncio.to_thread(self._record_sync, symbol, session_date, atm_iv, dte)
        except Exception:
            logger.exception("Failed to record ATM IV for %s", symbol)

    def _history_sync(self, symbol: str, limit: int) -> list[float]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT atm_iv FROM option_iv_history WHERE symbol = ? AND dte BETWEEN ? AND ? "
                "ORDER BY session_date DESC LIMIT ?",
                (symbol.upper(), COMPARABLE_DTE[0], COMPARABLE_DTE[1], limit),
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
