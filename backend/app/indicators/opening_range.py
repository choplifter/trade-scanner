"""The opening range of the session on screen -- the first five minutes'
high and low, the box the ORB strategy trades the break of.

Drawn so the strategy's box is checkable by eye: the Strategy Signal lines
show where a break's stop and target sit, but the range they broke out of
was invisible, and a reader could not tell a clean break of a tight box from
a wobble across a wide one.

Anchored to the latest session in the bars rather than to today, for the
same reason as the premarket range next door: on a Saturday the chart shows
Friday's candles, and Friday's opening range is the one that belongs on
them.
"""

from app.market_data import opening_range as orange
from app.market_data import session_marks

NAME = "Opening Range"
KIND = "level"
# Its own hue -- rose -- distinct from the premarket blue and the daily teal
# it will usually sit between.
COLORS = {"High": "#c2418f", "Low": "#c2418f"}
# How these lines are drawn. Width in pixels, dash one of solid / dotted /
# dashed / large-dashed / sparse-dotted. Omit either key for the default.
STYLE = {"width": 1, "dash": "dashed"}
# Five minutes of one session: the finest window anything here draws. One
# hourly candle contains it whole, so it is noise on every coarser chart.
MAX_TIMEFRAME = "1Min"

_EMPTY = {"High": None, "Low": None}


class _Bar:
    """The three fields opening_range reads, off a chart frame row."""

    __slots__ = ("high", "low", "timestamp")

    def __init__(self, timestamp, high, low):
        self.timestamp = timestamp
        self.high = high
        self.low = low


def compute(ctx) -> dict:
    """The latest session's opening range.

    The measurement lives in app.market_data.opening_range so this draws the
    same box the strategy trades -- see that module on why one implementation
    matters. During the window itself the range so far is drawn, same as the
    premarket range while the premarket is still running; it is settled five
    minutes into the session.
    """
    df = ctx.minute_bars
    if df.empty:
        return _EMPTY

    bars = [_Bar(row.timestamp, row.high, row.low) for row in df.itertuples()]
    session_date = session_marks.latest_session_date(bars)
    if session_date is None:
        return _EMPTY
    high, low = orange.opening_range(bars, session_date)
    return {"High": high, "Low": low}
