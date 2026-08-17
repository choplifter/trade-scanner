"""Shared input data and small utilities for indicator files -- built once
per /bars request (see app.routers.symbols) and handed to every indicator's
compute() so no indicator file has to fetch its own bars from Alpaca.

This module itself is excluded from the indicator loader's directory scan
(see app.indicators.loader) -- it's infrastructure, not an indicator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd


@dataclass
class IndicatorContext:
    symbol: str
    minute_bars: pd.DataFrame
    weekly_bars: pd.DataFrame
    monthly_bars: pd.DataFrame


_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "vwap"]


def _bars_to_df(bars: list) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=_COLUMNS)
    return pd.DataFrame(
        {
            "timestamp": [b.timestamp for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            # Alpaca's own per-bar trade-weighted price. Carried through
            # because it is a better answer to "what price did the money
            # trade at" than an H/L/C typical price, which weights a
            # one-tick wick the same as the bulk of the bar's volume.
            # None on feeds or bars that omit it -- an indicator using it
            # has to fall back rather than assume it is there.
            "vwap": [getattr(b, "vwap", None) for b in bars],
        }
    )


def build_context(
    symbol: str, minute_bars: list, weekly_bars: list, monthly_bars: list
) -> IndicatorContext:
    return IndicatorContext(
        symbol=symbol,
        minute_bars=_bars_to_df(minute_bars),
        weekly_bars=_bars_to_df(weekly_bars),
        monthly_bars=_bars_to_df(monthly_bars),
    )


def prior_completed_period(df: pd.DataFrame, period_end) -> pd.Series | None:
    """The most recent bar whose own period has fully elapsed as of now.

    Alpaca's native weekly/monthly bars are keyed by period *start*, and the
    most recent bar in a lookback window is often still in progress (e.g.
    this week's bar exists and updates intraday even though the week isn't
    over) -- indexing bars[-2] to skip it breaks the moment the market's
    closed and the current period has genuinely already completed, since
    then the last bar *is* already done and bars[-2] would wrongly skip a
    valid one. Checking each bar's own end (period_end(bar_start)) against
    now avoids that off-by-one regardless of market/session state.

    period_end: bar timestamp -> that bar's period end, e.g.
    `lambda ts: ts + pd.Timedelta(days=7)` for weekly,
    `lambda ts: ts + pd.DateOffset(months=1)` for monthly.
    """
    if df.empty:
        return None
    now = pd.Timestamp(datetime.now(timezone.utc))
    ends = df["timestamp"].apply(period_end)
    completed = df[ends <= now]
    if completed.empty:
        return None
    return completed.iloc[-1]
