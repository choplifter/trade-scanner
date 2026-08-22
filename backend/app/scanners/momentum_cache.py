"""Per-symbol trailing-window momentum cache for the live scanner
tables.

Mirrors app.market_data.news_cache.NewsCache: cache per symbol, refresh
on a slow interval, only ever asked about whatever's currently in a
ranked view (see ScannerEngine._attach_momentum), not the whole multi-
thousand-symbol universe -- fetching bars for every poll tick across
potentially thousands of symbols would be far too expensive.

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
from datetime import timedelta

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.market_data.bars import get_intraday_5m_bars_multi
from app.market_data.candle_shape import is_shaved_top
from app.market_data.momentum import (
    MOMENTUM_WINDOW,
    is_regular_session_bar,
    pct_change_over_window,
    same_trading_day,
)
from app.market_data.volume_surge import trailing_windows
from app.market_data.vwap import SessionVwapState

logger = logging.getLogger(__name__)


class MomentumCache:
    def __init__(self, settings: Settings, clients: AlpacaClients):
        self.settings = settings
        self.clients = clients
        self._momentum_pct: dict[str, float | None] = {}
        # All three computed from the same 5-minute bar fetch as
        # momentum_pct above (see app.market_data.bars.
        # MOMENTUM_BAR_TIMEFRAME), no extra API call -- see
        # formulas.is_momentum_alert (long side only: a fast upward move
        # confirmed by a shaved-top candle that's also green, trading
        # above the session VWAP).
        self._is_shaved_top: dict[str, bool] = {}
        self._is_green: dict[str, bool] = {}
        self._is_above_vwap: dict[str, bool] = {}
        # Volume in the trailing window and the window before it, from the
        # same bars -- the raw inputs for the acceleration fields this
        # module's docstring describes. Kept as raw volumes rather than a
        # ratio because turning them into rvol_1h needs avg_vol_20d and the
        # intraday volume profile, neither of which this cache holds; see
        # ScannerEngine._attach_momentum.
        self._window_volume: dict[str, float | None] = {}
        self._prior_window_volume: dict[str, float | None] = {}
        self._bars: dict[str, list] = {}
        self._fetched_at: dict[str, float] = {}

    def get(self, symbol: str) -> float | None:
        return self._momentum_pct.get(symbol)

    def is_shaved_top(self, symbol: str) -> bool:
        return self._is_shaved_top.get(symbol, False)

    def is_green(self, symbol: str) -> bool:
        return self._is_green.get(symbol, False)

    def is_above_vwap(self, symbol: str) -> bool:
        return self._is_above_vwap.get(symbol, False)

    def window_volume(self, symbol: str) -> float | None:
        return self._window_volume.get(symbol)

    def prior_window_volume(self, symbol: str) -> float | None:
        return self._prior_window_volume.get(symbol)

    def bars(self, symbol: str) -> list:
        """The cached 5-minute bars themselves, so a caller that needs a
        different window (rvol_1h computes its own expected volume from the
        same series) doesn't trigger a second fetch.
        """
        return self._bars.get(symbol) or []

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
            bars_by_symbol = await get_intraday_5m_bars_multi(self.clients, stale)
        except Exception:
            logger.exception("Scanner momentum refresh failed for a batch of %d symbols", len(stale))
            return

        window = timedelta(minutes=self.settings.scanner_volume_surge_window_minutes)
        for symbol in stale:
            self._fetched_at[symbol] = now
            bars = bars_by_symbol.get(symbol) or []
            self._bars[symbol] = bars
            # Same set-on-every-refresh discipline as the four fields below:
            # trailing_windows returns (None, None) outside the session, which
            # clears the reading rather than leaving yesterday's stuck.
            recent, prior = trailing_windows(bars, window)
            self._window_volume[symbol] = recent
            self._prior_window_volume[symbol] = prior
            # Only a regular-session bar may report momentum, matching the
            # backtest exactly (see momentum.is_regular_session_bar).
            # Outside 09:30-16:00 ET there is no meaningful "momentum right
            # now": a trailing momentum window would span the market close
            # and compare a thin after-hours print against the last
            # regular-session price. The window itself may still reach back
            # into the same day's premarket, which is what keeps the
            # opening range measurable (see same_trading_day).
            #
            # All four fields are set explicitly on every refresh (not
            # just when there's enough data) so a value that's no longer
            # computable -- a feed gap, or simply being outside the
            # session -- clears instead of sticking at a stale reading
            # forever, which also stops the alarm firing on one.
            latest_bar = bars[-1] if bars and is_regular_session_bar(bars[-1]) else None
            session_bars = [b for b in bars if same_trading_day(b, latest_bar)] if latest_bar else []
            self._momentum_pct[symbol] = pct_change_over_window(session_bars, MOMENTUM_WINDOW)
            if latest_bar is not None:
                self._is_shaved_top[symbol] = is_shaved_top(
                    latest_bar.open, latest_bar.high, latest_bar.low, latest_bar.close
                )
                self._is_green[symbol] = latest_bar.close > latest_bar.open
                # Same seed_from_bars(bars) pattern app.ai.trade_ideas uses
                # for the same reason: cheaply reuses this same bar fetch
                # (multi-day, oldest-first) rather than a second API call --
                # SessionVwapState resets on every calendar-date change, so
                # replaying the whole fetch still lands on just today's
                # running VWAP as of the latest bar. VWAP is computed from
                # these same 5-minute bars now, not raw 1-minute prints --
                # a genuinely cumulative volume-weighted figure either way,
                # just fed coarser inputs.
                vwap = SessionVwapState(symbol=symbol).seed_from_bars(bars)
                self._is_above_vwap[symbol] = vwap is not None and latest_bar.close > vwap
            else:
                self._is_shaved_top[symbol] = False
                self._is_green[symbol] = False
                self._is_above_vwap[symbol] = False
