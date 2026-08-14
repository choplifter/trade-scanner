"""Per-symbol trailing-15-minute momentum cache for the live scanner
tables.

Mirrors app.market_data.news_cache.NewsCache: cache per symbol, refresh
on a slow interval, only ever asked about whatever's currently in a
ranked view (see ScannerEngine._attach_momentum), not the whole multi-
thousand-symbol universe -- fetching minute bars for every poll tick
across potentially thousands of symbols would be far too expensive.

Exists alongside RVOL, not instead of it: RVOL is cumulative volume vs. a
full-day average and mechanically rises through the session (see
formulas.rvol's docstring), so it can't tell "the normal end-of-session
volume ramp every stock gets" from "this specific stock is genuinely
accelerating right now." A 2026-08-14 investigation into a late-session
SPY volume spike found the day's already-ranked gainers had, on average,
faded rather than extended into the close, while a handful of individual
names were still freshly running -- this field is what would have
surfaced those specific names instead of the whole-day leaderboard.
"""

import logging
import time
from collections.abc import Iterable

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.market_data.bars import get_intraday_minute_bars_multi
from app.market_data.candle_shape import is_marubozu as _is_marubozu_bar
from app.market_data.momentum import MOMENTUM_WINDOW, pct_change_over_window

logger = logging.getLogger(__name__)


class MomentumCache:
    def __init__(self, settings: Settings, clients: AlpacaClients):
        self.settings = settings
        self.clients = clients
        self._pct_change_15m: dict[str, float | None] = {}
        # Whether the latest 1-minute bar was a marubozu (see
        # app.market_data.candle_shape) -- computed from the same bar fetch
        # as pct_change_15m above, no extra API call.
        self._is_marubozu: dict[str, bool] = {}
        self._fetched_at: dict[str, float] = {}

    def get(self, symbol: str) -> float | None:
        return self._pct_change_15m.get(symbol)

    def is_marubozu(self, symbol: str) -> bool:
        return self._is_marubozu.get(symbol, False)

    async def ensure_fresh(self, symbols: Iterable[str]) -> None:
        now = time.monotonic()
        stale = [
            s
            for s in symbols
            if now - self._fetched_at.get(s, -float("inf"))
            >= self.settings.scanner_momentum_refresh_interval
        ]
        if not stale:
            return

        try:
            bars_by_symbol = await get_intraday_minute_bars_multi(self.clients, stale)
        except Exception:
            logger.exception("Scanner momentum refresh failed for a batch of %d symbols", len(stale))
            return

        for symbol in stale:
            self._fetched_at[symbol] = now
            bars = bars_by_symbol.get(symbol) or []
            # Explicitly set both (not just when there's enough data) so a
            # value that's no longer computable (e.g. a feed gap) correctly
            # clears instead of sticking at a stale reading forever.
            self._pct_change_15m[symbol] = pct_change_over_window(bars, MOMENTUM_WINDOW)
            latest_bar = bars[-1] if bars else None
            self._is_marubozu[symbol] = (
                _is_marubozu_bar(latest_bar.open, latest_bar.high, latest_bar.low, latest_bar.close)
                if latest_bar is not None
                else False
            )
