"""Bollinger Bands on intraday minute closes: a 20-period simple moving
average with a band two standard deviations either side of it.

The middle line is the same mean a 20-EMA approximates, so the value here
is the envelope: it widens when the last twenty bars disagreed and pinches
when they did not, which is the one thing a moving average alone cannot
say. A touch of a band is not a signal -- in a trend price walks the upper
band for hours -- so this draws the envelope and leaves the reading to the
reader.

Intraday only (MAX_TIMEFRAME): the bands describe the last twenty bars,
and on a weekly chart those twenty bars are five months, by which point the
envelope says nothing about today. "1Min" rather than "1Hour" because the
chart draws series indicators on the minute feed alone (ChartWidget keeps
only "level" kinds above it) -- computing them for a request that discards
them would be work nobody sees.
"""

import pandas_ta as ta

NAME = "Bollinger"
KIND = "series"
MAX_TIMEFRAME = "1Min"
LENGTH = 20
STDEV = 2.0
# The bands muted, the mean plainer still: this is background, drawn
# behind the price action rather than competing with it.
COLORS = {
    "Bollinger upper": "#7c93b8",
    "Bollinger middle": "#8a8f99",
    "Bollinger lower": "#7c93b8",
}
STYLE = {"width": 1, "dash": "dotted"}


def _to_points(df, series) -> list[dict]:
    return [
        {"t": ts.isoformat(), "value": None if v != v else float(v)}  # v != v -> NaN
        for ts, v in zip(df["timestamp"], series)
    ]


def compute(ctx) -> dict:
    df = ctx.minute_bars
    empty = {"Bollinger upper": [], "Bollinger middle": [], "Bollinger lower": []}
    if df.empty or len(df) < LENGTH:
        # Fewer bars than the window means every value would be NaN; an
        # empty series draws nothing, which is the honest answer.
        return empty
    bands = ta.bbands(df["close"], length=LENGTH, std=STDEV)
    if bands is None or bands.empty:
        return empty
    # pandas_ta names the columns by their parameters (BBL_20_2.0 and so
    # on); position is stable across versions where the suffix is not.
    lower, middle, upper = bands.iloc[:, 0], bands.iloc[:, 1], bands.iloc[:, 2]
    return {
        "Bollinger upper": _to_points(df, upper),
        "Bollinger middle": _to_points(df, middle),
        "Bollinger lower": _to_points(df, lower),
    }
