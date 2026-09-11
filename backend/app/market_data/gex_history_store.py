"""One net-gamma reading per symbol per session, so today's number has
something to be measured against.

Net GEX on its own is a quantity without a scale. Minus four billion means
nothing until you know that this symbol has run between minus eight and
plus two over the past months -- the same problem an implied volatility
reading has, and solved the same way here as in
app.options.iv_history_store: record one value per session as readings are
served, and answer with a percentile only once there are enough of them to
make a range rather than a coincidence.

The rank helper is imported from that module rather than rewritten, so a
GEX percentile and an IV rank cannot drift apart in their arithmetic.

Best-effort by design: recording is a side effect of somebody looking at a
symbol, and a failed write is logged and dropped rather than allowed to
take a reading down with it.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date, datetime, timezone

from app.options.iv_history_store import IvRank, rank_within

logger = logging.getLogger(__name__)

# Same floor as the IV rank, for the same reason: below roughly a trading
# month the highest and lowest readings are a sample artefact.
MIN_SAMPLES = 20
LOOKBACK_SESSIONS = 252

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gex_history (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    net_gex REAL NOT NULL,
    spot_price REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (symbol, session_date)
);
"""


class GexHistoryStore:
    """Net GEX per symbol and session, and the percentile of a reading
    within what has been recorded."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    async def init_schema(self) -> None:
        def run() -> None:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)

        await asyncio.to_thread(run)

    async def record(self, symbol: str, session_date: date, net_gex: float, spot_price: float) -> None:
        """Today's reading for this symbol. One row per session -- the last
        write of the day wins, which is the reading closest to the close."""

        def run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO gex_history (symbol, session_date, net_gex, spot_price, recorded_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, session_date) DO UPDATE SET
                        net_gex = excluded.net_gex,
                        spot_price = excluded.spot_price,
                        recorded_at = excluded.recorded_at
                    """,
                    (
                        symbol.upper(),
                        session_date.isoformat(),
                        float(net_gex),
                        float(spot_price),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

        try:
            await asyncio.to_thread(run)
        except Exception:
            logger.exception("Recording GEX history failed for %s", symbol)

    async def history(self, symbol: str, limit: int = LOOKBACK_SESSIONS) -> list[float]:
        def run() -> list[float]:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT net_gex FROM gex_history
                    WHERE symbol = ?
                    ORDER BY session_date DESC
                    LIMIT ?
                    """,
                    (symbol.upper(), limit),
                ).fetchall()
            return [row[0] for row in rows]

        try:
            return await asyncio.to_thread(run)
        except Exception:
            logger.exception("Reading GEX history failed for %s", symbol)
            return []

    async def rank(self, symbol: str, current: float | None) -> tuple[IvRank | None, int]:
        """(percentile of `current` within the recorded range, samples).
        None below MIN_SAMPLES -- "not enough history yet", never zero."""
        if current is None:
            return None, 0
        past = await self.history(symbol)
        return rank_within(current, past, min_samples=MIN_SAMPLES), len(past)


__all__ = ["GexHistoryStore", "LOOKBACK_SESSIONS", "MIN_SAMPLES"]
