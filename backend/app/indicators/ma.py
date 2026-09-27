"""9- and 20-period EMA on the chart's own closes -- the two most common
short-term trend references overlaid on a chart. Minute closes on an
intraday chart, hourly/daily/... closes above it (ctx.chart_bars), so the
line always belongs to the candles it is drawn over.
"""

import pandas_ta as ta

NAME = "EMA"
KIND = "series"
COLORS = {"EMA 9": "#2eb872", "EMA 20": "#d6455a"}
# The formula, for the intraday chart to recompute live from its own
# candles -- see STUDY in app.indicators.loader.
STUDY = {"EMA 9": {"type": "ema", "length": 9}, "EMA 20": {"type": "ema", "length": 20}}


def _to_points(df, series) -> list[dict]:
    return [
        {"t": ts.isoformat(), "value": None if v != v else float(v)}  # v != v -> NaN
        for ts, v in zip(df["timestamp"], series)
    ]


def compute(ctx) -> dict:
    df = ctx.chart_bars
    if df.empty:
        return {"EMA 9": [], "EMA 20": []}
    ema9 = ta.ema(df["close"], length=STUDY["EMA 9"]["length"])
    ema20 = ta.ema(df["close"], length=STUDY["EMA 20"]["length"])
    return {"EMA 9": _to_points(df, ema9), "EMA 20": _to_points(df, ema20)}
