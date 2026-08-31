"""Prior completed week's high/low/close (Alpaca's native weekly bars) -- a
classic swing-trading reference level, distinct from any single day's
range.

The close is carried alongside the range because the two answer different
questions: the high and low bound where price *went* last week, while the
close is where it was finally accepted -- the level a new week gaps away
from and often returns to. It comes off the same bar, so reporting it costs
nothing extra.
"""

import pandas as pd

from app.indicators.context import prior_completed_period

NAME = "Weekly Range"
KIND = "level"
# Close in a lighter shade of the same purple: same period, different kind of
# level, and it should not read as a third boundary of the range.
COLORS = {"High": "#7a4fd6", "Low": "#7a4fd6", "Close": "#a98ae8"}
# How these lines are drawn. Width in pixels, dash one of solid / dotted /
# dashed / large-dashed / sparse-dotted. Omit either key for the default.
STYLE = {"width": 1, "dash": "dashed"}
# Same reasoning as the daily range one step up: a week fits inside one
# monthly candle.
MAX_TIMEFRAME = "1Week"

_EMPTY = {"High": None, "Low": None, "Close": None}


def compute(ctx) -> dict:
    bar = prior_completed_period(ctx.weekly_bars, lambda ts: ts + pd.Timedelta(days=7), now=ctx.as_of)
    if bar is None:
        return _EMPTY
    return {
        "High": float(bar["high"]),
        "Low": float(bar["low"]),
        "Close": float(bar["close"]),
    }
