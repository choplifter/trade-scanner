"""Today's premarket high/low -- the range price action outside regular
hours has set up, a common reference for the open.
"""

from datetime import datetime

from app.market_data.bars import today_premarket_start_utc
from app.services.market_clock import ET, trading_hours_for

NAME = "Premarket Range"
KIND = "level"
COLORS = {"High": "#2a78d6", "Low": "#2a78d6"}


def compute(ctx) -> dict:
    df = ctx.minute_bars
    if df.empty:
        return {"High": None, "Low": None}

    hours = trading_hours_for(datetime.now(ET).date())
    if hours is None:
        return {"High": None, "Low": None}
    market_open = hours[0]

    start = today_premarket_start_utc()
    pm = df[(df["timestamp"] >= start) & (df["timestamp"] < market_open)]
    if pm.empty:
        return {"High": None, "Low": None}
    return {"High": float(pm["high"].max()), "Low": float(pm["low"].min())}
