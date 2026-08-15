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
from app.market_data.bars import aggregate_last_n_minutes, get_intraday_minute_bars_multi
from app.market_data.candle_shape import is_shaved_top
from app.market_data.momentum import MOMENTUM_WINDOW, pct_change_over_window
from app.market_data.vwap import SessionVwapState

logger = logging.getLogger(__name__)

# The shape/color confirmation candle is built from 5 one-minute bars
# rather than just the latest single one -- a lone 1-minute print is noisy
# and wick-prone, while a 5-minute candle is the smallest size that still
# reads as a real, chart-recognizable candle (matches this app's own 5m
# chart default, see lightweight_chart.html's DEFAULT_KEY).
CONFIRMATION_CANDLE_MINUTES = 5


class MomentumCache:
    def __init__(self, settings: Settings, clients: AlpacaClients):
        self.settings = settings
        self.clients = clients
        self._pct_change_15m: dict[str, float | None] = {}
        # All three computed from the same bar fetch as pct_change_15m
        # above, no extra API call -- see formulas.is_momentum_alert (long
        # side only: a fast upward move confirmed by a shaved-top candle
        # that's also green, trading above the session VWAP).
        self._is_shaved_top: dict[str, bool] = {}
        self._is_green: dict[str, bool] = {}
        self._is_above_vwap: dict[str, bool] = {}
        self._fetched_at: dict[str, float] = {}

    def get(self, symbol: str) -> float | None:
        return self._pct_change_15m.get(symbol)

    def is_shaved_top(self, symbol: str) -> bool:
        return self._is_shaved_top.get(symbol, False)

    def is_green(self, symbol: str) -> bool:
        return self._is_green.get(symbol, False)

    def is_above_vwap(self, symbol: str) -> bool:
        return self._is_above_vwap.get(symbol, False)

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
            # Explicitly set all four (not just when there's enough data)
            # so a value that's no longer computable (e.g. a feed gap)
            # correctly clears instead of sticking at a stale reading
            # forever.
            self._pct_change_15m[symbol] = pct_change_over_window(bars, MOMENTUM_WINDOW)
            candle = aggregate_last_n_minutes(bars, CONFIRMATION_CANDLE_MINUTES)
            if candle is not None:
                self._is_shaved_top[symbol] = is_shaved_top(candle.open, candle.high, candle.low, candle.close)
                self._is_green[symbol] = candle.close > candle.open
                # Same seed_from_bars(bars) pattern app.ai.trade_ideas uses
                # for the same reason: cheaply reuses this same bar fetch
                # (multi-day, oldest-first) rather than a second API call --
                # SessionVwapState resets on every calendar-date change, so
                # replaying the whole fetch still lands on just today's
                # running VWAP as of the latest bar. Fed the raw 1-minute
                # bars, not the aggregated candle -- VWAP is a genuinely
                # cumulative volume-weighted figure, not something that
                # should be computed from coarser buckets.
                vwap = SessionVwapState(symbol=symbol).seed_from_bars(bars)
                self._is_above_vwap[symbol] = vwap is not None and candle.close > vwap
            else:
                self._is_shaved_top[symbol] = False
                self._is_green[symbol] = False
                self._is_above_vwap[symbol] = False
