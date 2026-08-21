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


# Chart timeframes, finest to coarsest. The ordering is what lets an
# indicator declare a single ceiling ("not above this") instead of
# enumerating every timeframe it applies to -- see MAX_TIMEFRAME in
# app.indicators.loader.
#
# 1m/5m/15m are all fetched as "1Min" and bucketed client-side (see
# utils/aggregateBars in the frontend), so they arrive here as one key.
TIMEFRAME_ORDER: tuple[str, ...] = ("1Min", "1Hour", "4Hour", "1Day", "1Week", "1Month")


def timeframe_rank(timeframe: str) -> int:
    """Position on TIMEFRAME_ORDER.

    An unrecognised timeframe ranks finest rather than raising, so adding a
    new chart resolution shows every indicator until someone decides
    otherwise -- the opposite default would silently blank the whole panel.
    """
    try:
        return TIMEFRAME_ORDER.index(timeframe)
    except ValueError:
        return 0


@dataclass
class IndicatorContext:
    symbol: str
    minute_bars: pd.DataFrame
    weekly_bars: pd.DataFrame
    monthly_bars: pd.DataFrame
    # Hourly bars, alongside the weekly and monthly ones above: one more
    # resolution an indicator can choose, not a property of the request.
    #
    # This is the structural anchor. A level is worth trading because it held
    # on a timeframe with real participation, and it has to stay put while
    # you drop to a faster chart to time the entry -- so the indicator that
    # draws it reads this regardless of what the chart is showing. See
    # app.indicators.market_structure.
    hourly_bars: pd.DataFrame
    # Which chart resolution the caller asked for. Indicators do not read
    # this themselves -- the loader uses it to decide which of them run at
    # all, so an indicator's compute() never has to think about zoom.
    timeframe: str = "1Min"


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
    symbol: str,
    minute_bars: list,
    weekly_bars: list,
    monthly_bars: list,
    timeframe: str = "1Min",
    hourly_bars: list | None = None,
) -> IndicatorContext:
    return IndicatorContext(
        symbol=symbol,
        minute_bars=_bars_to_df(minute_bars),
        weekly_bars=_bars_to_df(weekly_bars),
        monthly_bars=_bars_to_df(monthly_bars),
        timeframe=timeframe,
        hourly_bars=_bars_to_df(hourly_bars or []),
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
