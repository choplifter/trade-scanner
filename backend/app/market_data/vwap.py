"""Session VWAP, tracked from two anchors at once.

The 09:30 ET regular-session anchor is what day-trading methodologies (Bear
Bull Trader, Warrior Trading) teach, and it stays the default everywhere the
app makes a decision. But it is not the only convention, and on a gapper the
difference is not cosmetic: IPST on 2026-08-17 traded 53.7M of its 103.9M
shares outside regular hours at an implied VWAP of 6.98, against 7.81 inside
the session. Anchoring at the premarket open pulled VWAP from 7.81 to ~7.18
and put a 7.39 close on the *other side* of the line -- which is why the
chart disagreed with TradingView, whose line runs from premarket.

Both are correct answers to different questions, so this accumulates both
rather than picking one. One state object, one reset, two running totals:
`update()` returns the regular-session figure (unchanged for every existing
caller) and `premarket_anchored_vwap` exposes the other. Keeping them in one
object matters because StreamManager holds exactly one accumulator per symbol
and seeds it from the /bars replay -- two objects would mean two seeds and
two chances to drift apart.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.services.market_clock import ET


def _typical_price(high: float, low: float, close: float) -> float:
    return (high + low + close) / 3.0


@dataclass
class SessionVwapState:
    symbol: str
    cum_pv: float = 0.0
    cum_vol: float = 0.0
    # The same accumulation with no session filter, so it includes premarket
    # and after-hours prints.
    cum_pv_premarket: float = 0.0
    cum_vol_premarket: float = 0.0
    session_date: object = field(default=None)

    def _maybe_reset(self, bar_ts_et: datetime) -> None:
        if self.session_date != bar_ts_et.date():
            self.session_date = bar_ts_et.date()
            self.cum_pv = 0.0
            self.cum_vol = 0.0
            self.cum_pv_premarket = 0.0
            self.cum_vol_premarket = 0.0

    def update(
        self,
        timestamp: datetime,
        high: float,
        low: float,
        close: float,
        volume: float,
        bar_vwap: float | None = None,
    ) -> float | None:
        """Feed one closed minute bar, return the running session VWAP.

        Alpaca bars already carry a per-bar trade-weighted vwap field, which
        is a better proxy for "money traded at" than the H/L/C typical price,
        so prefer it when present and fall back to typical price otherwise.
        """
        ts_et = timestamp.astimezone(ET)
        self._maybe_reset(ts_et)

        price = bar_vwap if bar_vwap is not None else _typical_price(high, low, close)
        # The premarket-anchored total takes every print, whatever the hour.
        self.cum_pv_premarket += price * volume
        self.cum_vol_premarket += volume

        if ts_et.time() < ts_et.replace(hour=9, minute=30, second=0, microsecond=0).time():
            # Pre-market bars don't count toward the *regular-session* VWAP
            # by convention -- just track the reset date and skip.
            return None if self.cum_vol == 0 else self.cum_pv / self.cum_vol

        self.cum_pv += price * volume
        self.cum_vol += volume
        if self.cum_vol <= 0:
            return None
        return self.cum_pv / self.cum_vol

    @property
    def premarket_anchored_vwap(self) -> float | None:
        """VWAP counting every print today, premarket included -- the line
        TradingView draws. None until something has traded.
        """
        if self.cum_vol_premarket <= 0:
            return None
        return self.cum_pv_premarket / self.cum_vol_premarket

    def seed_from_bars(self, bars: list) -> float | None:
        """Replay a day's worth of minute bars (oldest first) to catch the
        accumulator up to "now" when a chart widget first opens.
        """
        result = None
        for bar in bars:
            result = self.update(
                timestamp=bar.timestamp,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                bar_vwap=getattr(bar, "vwap", None),
            )
        return result
