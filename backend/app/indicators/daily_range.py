"""Prior completed trading day's regular-session high/low -- PDH/PDL.

The day-scale sibling of the weekly and monthly ranges, and the level most
day-trading setups are stated against: a break of yesterday's high, or a
rejection at yesterday's low.

Regular session only (09:30-16:00 ET, or the calendar's early close on a
half day). Extended-hours prints are deliberately excluded: they are thin
enough that one stray tick can set a "high" no meaningful volume traded at,
and today's premarket already has its own indicator.

Computed from the minute bars the chart request already fetched -- those
cover ten sessions (see bars._INTRADAY_SESSION_LOOKBACK), so no extra Alpaca
call is needed for one day back.
"""

from datetime import datetime

from app.market_data import session_marks
from app.services.market_clock import ET, trading_hours_for

NAME = "Daily Range"
KIND = "level"
COLORS = {"High": "#2f9c8f", "Low": "#2f9c8f"}
# How these lines are drawn. Width in pixels, dash one of solid / dotted /
# dashed / large-dashed / sparse-dotted. Omit either key for the default.
STYLE = {"width": 1, "dash": "dashed"}
# A day's range is contained inside a single weekly candle, so above 1Day
# these lines describe something the chart cannot show being crossed.
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
    """Yesterday's regular-session high and low.

    The measurement lives in app.market_data.session_marks so a strategy can
    aim at the same number this draws. "Yesterday" is relative to a session
    date: today while today is still running, which reproduces what
    prior_completed_period did here before -- and once today has closed, its
    own range becomes the prior session on the next trading date, exactly as
    the calendar rolls.
    """
    df = ctx.minute_bars
    if df.empty:
        return _EMPTY

    bars = [_Bar(row.timestamp, row.high, row.low) for row in df.itertuples()]
    # Deliberately the wall clock, unlike premarket_range next door: this
    # indicator's contract is "the most recently *completed* session", so
    # after Friday's close it shows Friday. See prior_completed_period, whose
    # docstring records why skipping the last row unconditionally is wrong,
    # and test_picks_the_most_recently_completed_session, which pins it.
    #
    # Worth knowing that this makes it disagree with the level a strategy
    # aims at: session_marks.prior_session_range answers "the day before the
    # session being traded", which on a Saturday is Thursday, not Friday.
    # Two different questions, both correct.
    today = datetime.now(ET).date()
    high, low = session_marks.prior_session_range(bars, today)
    if high is None:
        # Nothing before today in the window. Today itself may nonetheless
        # have completed -- the market is closed and its session is over --
        # in which case it is the most recent finished day and the one the
        # chart should mark.
        hours = trading_hours_for(today)
        if hours is not None and datetime.now(ET) >= hours[1]:
            session = [
                b for b in bars if hours[0] <= b.timestamp.astimezone(ET) <= hours[1]
            ]
            if session:
                return {
                    "High": max(b.high for b in session),
                    "Low": min(b.low for b in session),
                }
        return _EMPTY
    return {"High": high, "Low": low}
