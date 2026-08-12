"""Prior completed month's high/low (Alpaca's native monthly bars) -- a
longer-horizon reference level than the weekly/premarket ranges.
"""

import pandas as pd

from app.indicators.context import prior_completed_period

NAME = "Monthly Range"
KIND = "level"
COLORS = {"High": "#c9862f", "Low": "#c9862f"}


def compute(ctx) -> dict:
    bar = prior_completed_period(ctx.monthly_bars, lambda ts: ts + pd.DateOffset(months=1))
    if bar is None:
        return {"High": None, "Low": None}
    return {"High": float(bar["high"]), "Low": float(bar["low"])}
