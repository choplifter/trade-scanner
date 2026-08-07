"""Session-anchored VWAP, reset at the 09:30 ET regular-session open -- the
standard definition day-trading methodologies (Bear Bull Trader, Warrior
Trading) teach and what every retail charting platform renders.
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
    session_date: object = field(default=None)

    def _maybe_reset(self, bar_ts_et: datetime) -> None:
        if self.session_date != bar_ts_et.date():
            self.session_date = bar_ts_et.date()
            self.cum_pv = 0.0
            self.cum_vol = 0.0

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

        if ts_et.time() < ts_et.replace(hour=9, minute=30, second=0, microsecond=0).time():
            # Pre-market bars don't count toward the *regular-session* VWAP
            # by convention -- just track the reset date and skip.
            return None if self.cum_vol == 0 else self.cum_pv / self.cum_vol

        price = bar_vwap if bar_vwap is not None else _typical_price(high, low, close)
        self.cum_pv += price * volume
        self.cum_vol += volume
        if self.cum_vol <= 0:
            return None
        return self.cum_pv / self.cum_vol

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
