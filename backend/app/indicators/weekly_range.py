"""Prior completed week's high/low (Alpaca's native weekly bars) -- a
classic swing-trading reference level, distinct from any single day's
range.
"""

import pandas as pd

from app.indicators.context import prior_completed_period

NAME = "Weekly Range"
KIND = "level"
COLORS = {"High": "#7a4fd6", "Low": "#7a4fd6"}


def compute(ctx) -> dict:
    bar = prior_completed_period(ctx.weekly_bars, lambda ts: ts + pd.Timedelta(days=7))
    if bar is None:
        return {"High": None, "Low": None}
    return {"High": float(bar["high"]), "Low": float(bar["low"])}
