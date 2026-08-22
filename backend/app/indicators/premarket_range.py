"""The premarket high/low of the session on screen -- the range price action
outside regular hours has set up, a common reference for the open.

Anchored to the latest session in the bars rather than to today. During a
live session those are the same date; outside one they are not, and the
wall-clock version simply drew nothing -- so on a Saturday the chart showed
Friday's candles with no premarket line, while a strategy targeting Friday's
premarket high pointed at a level the reader had no way to see.
"""

from app.market_data import session_marks

NAME = "Premarket Range"
KIND = "level"
COLORS = {"High": "#2a78d6", "Low": "#2a78d6"}
# How these lines are drawn. Width in pixels, dash one of solid / dotted /
# dashed / large-dashed / sparse-dotted. Omit either key for the default.
STYLE = {"width": 1, "dash": "dashed"}
# Today's premarket is a fraction of one session, so it is already the
# finest-grained of the range levels -- a daily candle contains it whole.
MAX_TIMEFRAME = "1Day"

_EMPTY = {"High": None, "Low": None}


class _Bar:
    """The three fields session_marks reads, off a chart frame row."""

    __slots__ = ("high", "low", "timestamp")

    def __init__(self, timestamp, high, low):
        self.timestamp = timestamp
        self.high = high
        self.low = low


def compute(ctx) -> dict:
    """Today's premarket range.

    The measurement lives in app.market_data.session_marks so a strategy can
    aim at the same number this draws -- see that module on why one
    implementation matters. Here the session date is simply today; a replay
    passes the day it is walking.
    """
    df = ctx.minute_bars
    if df.empty:
        return _EMPTY

    bars = [_Bar(row.timestamp, row.high, row.low) for row in df.itertuples()]
    session_date = session_marks.latest_session_date(bars)
    if session_date is None:
        return _EMPTY
    high, low = session_marks.premarket_range(bars, session_date)
    return {"High": high, "Low": low}
