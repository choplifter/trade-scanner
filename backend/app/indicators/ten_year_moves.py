"""Where the 10-year Treasury yield moved hard, marked on the candle it
moved in: an arrow up in red where yields jumped, down in green where they
fell -- red and green for what the move tends to mean for index prices,
the arrow for which way the yield itself went.

Rates are the discount rate under every valuation, and a sharp move in
them is the most common outside reason for a sharp SPY or QQQ candle.
Marking the candle answers "was that rates?" without a second chart.

What counts as sharp depends on the candle: the markers are derived by the
chart (see DERIVE in app.indicators.loader, and utils/moveMarkers.ts),
because a 15m chart's candles are built client-side from the minute feed.
This file hands it the yield itself -- minute points from Cboe for the
latest session under an intraday or hourly chart (Cboe keeps no older
minute history, so earlier sessions carry no markers), daily closes back
to 1993 under anything coarser.

The threshold is 1.5 standard deviations of a typical move over that
candle's length: the 10-year's daily moves run about 7 bp, scaled by the
square root of time to the candle, with a 2 bp floor so the minute chart is
not marked for every tick of a quiet morning. That puts 15m at 2 bp, 1h at
about 4, a day at about 10 and a week at about 23.
"""

from datetime import timedelta

NAME = "10Y Moves"
KIND = "marker"
COLORS = {"Rising": "#d1242f", "Falling": "#1a7f37"}
DERIVE = {
    "type": "moves",
    "label": "10Y",
    # Points are percent; the markers speak basis points.
    "unit": "bp",
    "per_unit": 100,
    "daily_sigma": 7.0,
    "k": 1.5,
    "floor": 2.0,
}

# The minute points only matter under charts whose candles are an hour or
# less; above that the daily closes are what a candle spans.
_INTRADAY_TIMEFRAMES = {"1Min", "1Hour", "4Hour"}
# Enough before the first candle for its own "previous close".
_LEAD = timedelta(days=45)


def compute(ctx) -> dict:
    if not ctx.ten_year:
        return {}
    points = ctx.ten_year
    chart = ctx.chart_bars
    if chart is not None and not chart.empty:
        start = chart["timestamp"].iloc[0] - _LEAD
        points = [p for p in points if p.at >= start]
    points = [p for p in points if p.at <= ctx.as_of]
    return {"10Y": [{"t": p.at.isoformat(), "value": p.value} for p in points]}


def wants_intraday(timeframe: str) -> bool:
    """Which of the two histories the caller should hand this chart."""
    return timeframe in _INTRADAY_TIMEFRAMES
