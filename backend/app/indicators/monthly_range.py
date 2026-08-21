"""Prior completed month's high/low (Alpaca's native monthly bars) -- a
longer-horizon reference level than the weekly/premarket ranges.
"""

import pandas as pd

from app.indicators.context import prior_completed_period

NAME = "Monthly Range"
KIND = "level"
COLORS = {"High": "#c9862f", "Low": "#c9862f"}
# How these lines are drawn. Width in pixels, dash one of solid / dotted /
# dashed / large-dashed / sparse-dotted. Omit either key for the default.
STYLE = {"width": 1, "dash": "dashed"}
# The coarsest timeframe the chart offers, so this one is never filtered --
# declared anyway rather than omitted, so the ceiling is stated for every
# range indicator instead of being absent on the one that happens not to
# need it.
MAX_TIMEFRAME = "1Month"


def compute(ctx) -> dict:
    bar = prior_completed_period(ctx.monthly_bars, lambda ts: ts + pd.DateOffset(months=1))
    if bar is None:
        return {"High": None, "Low": None}
    return {"High": float(bar["high"]), "Low": float(bar["low"])}
