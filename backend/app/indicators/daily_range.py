"""Prior completed trading day's regular-session high/low -- PDH/PDL.

The day-scale sibling of the weekly and monthly ranges, and the level most
day-trading setups are stated against: a break of yesterday's high, or a
rejection at yesterday's low.

Regular session only (09:30-16:00 ET, or the calendar's early close on a
half day). Extended-hours prints are deliberately excluded: they are thin
enough that one stray tick can set a "high" no meaningful volume traded at,
and today's premarket already has its own indicator.

Computed from the minute bars the chart request already fetched -- those
cover ten sessions (see bars._INTRADAY_SESSION_LOOKBACK), so no extra Alpaca
call is needed for one day back.
"""

import pandas as pd

from app.indicators.context import prior_completed_period
from app.services.market_clock import ET, trading_hours_for

NAME = "Daily Range"
KIND = "level"
COLORS = {"High": "#2f9c8f", "Low": "#2f9c8f"}

_EMPTY = {"High": None, "Low": None}


def compute(ctx) -> dict:
    df = ctx.minute_bars
    if df.empty:
        return _EMPTY

    stamps = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(ET)

    closes: list[pd.Timestamp] = []
    highs: list[float] = []
    lows: list[float] = []
    for day in sorted({stamp.date() for stamp in stamps}):
        hours = trading_hours_for(day)
        if hours is None:  # weekend or holiday -- no regular session to measure
            continue
        market_open, market_close = hours
        session = df[(stamps >= market_open) & (stamps <= market_close)]
        if session.empty:
            continue
        # Keyed by session *close* rather than open, so the shared
        # "has this period elapsed" test below is a direct comparison
        # against now and needs no period_end arithmetic of its own.
        closes.append(pd.Timestamp(market_close))
        highs.append(float(session["high"].max()))
        lows.append(float(session["low"].min()))

    if not closes:
        return _EMPTY

    daily = pd.DataFrame({"timestamp": closes, "high": highs, "low": lows})
    # Same semantics as the weekly/monthly ranges: the most recent period
    # that has actually finished. Today is therefore excluded while its
    # session is still running, and included once it has closed -- which is
    # the behaviour prior_completed_period exists to get right, rather than
    # unconditionally skipping the last row.
    bar = prior_completed_period(daily, lambda ts: ts)
    if bar is None:
        return _EMPTY
    return {"High": float(bar["high"]), "Low": float(bar["low"])}
