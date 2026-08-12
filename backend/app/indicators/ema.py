"""9- and 20-period EMA on intraday minute closes -- the two most common
short-term trend references day traders overlay on a chart.
"""

import pandas_ta as ta

NAME = "EMA"
KIND = "series"
COLORS = {"EMA 9": "#2eb872", "EMA 20": "#d6455a"}


def _to_points(df, series) -> list[dict]:
    return [
        {"t": ts.isoformat(), "value": None if v != v else float(v)}  # v != v -> NaN
        for ts, v in zip(df["timestamp"], series)
    ]


def compute(ctx) -> dict:
    df = ctx.minute_bars
    if df.empty:
        return {"EMA 9": [], "EMA 20": []}
    ema9 = ta.ema(df["close"], length=9)
    ema20 = ta.ema(df["close"], length=20)
    return {"EMA 9": _to_points(df, ema9), "EMA 20": _to_points(df, ema20)}
