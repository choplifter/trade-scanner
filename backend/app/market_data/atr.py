"""Average daily true range, from the intraday bars already in hand.

The number Aziz reads off his scanner's ATR column: how far this stock
typically travels in a day. The opening-range rule compares its five-minute
box against it -- a range that already covers half the day's usual travel
leaves no room for the trade the breakout is supposed to catch.

Computed from the same 5-minute (or finer) bars every walk already holds,
aggregated to regular-session days, rather than from a separate daily-bar
fetch: the walks are look-ahead-safe precisely because everything they see
is the bar list up to now, and a second data source would need its own
as-of discipline. Extended-hours prints are excluded the same way
session_marks excludes them from the prior day's range -- one thin premarket
tick would set a daily "high" no meaningful volume traded at and inflate
every range it touches.

Parameterised by session date like session_marks, and only sessions strictly
before it count, so replaying a day uses the ATR that was knowable at its
open.
"""

from app.services.market_clock import ET, trading_hours_for

# The conventional window, and what the Trade-Ideas column the method was
# stated against shows. Fewer sessions than this still produce an answer --
# the chart's minute frame holds about ten -- down to the floor below.
WINDOW_SESSIONS = 14

# Below this many completed sessions the average is one or two prints
# pretending to be a typical day, so the answer is None -- "unknowable", not
# zero. What a rule does with None is its own documented choice; see the
# opening-range rule for the one taken there.
MIN_SESSIONS = 5


def daily_atr(
    bars: list,
    session_date,
    window: int = WINDOW_SESSIONS,
    min_sessions: int = MIN_SESSIONS,
) -> float | None:
    """The average true range of the last `window` completed regular sessions
    strictly before `session_date`, or None if fewer than `min_sessions` are
    in view.

    True range, not high minus low: a gap is travel too, so each day's range
    is widened to reach the previous session's close when the open jumped
    over it. On the first session in view there is no previous close and the
    bar's own range stands.
    """
    days: dict = {}
    for bar in bars:
        ts = bar.timestamp.astimezone(ET)
        day = ts.date()
        if day >= session_date:
            continue
        hours = trading_hours_for(day)
        if hours is None or not (hours[0] <= ts <= hours[1]):
            continue
        entry = days.get(day)
        if entry is None:
            days[day] = [bar.high, bar.low, bar.close, ts]
        else:
            entry[0] = max(entry[0], bar.high)
            entry[1] = min(entry[1], bar.low)
            if ts >= entry[3]:
                entry[2], entry[3] = bar.close, ts

    ordered = sorted(days)
    if len(ordered) < min_sessions:
        return None

    ranges = []
    for prev_day, day in zip([None, *ordered], ordered):
        high, low, _, _ = days[day]
        true_range = high - low
        if prev_day is not None:
            prev_close = days[prev_day][2]
            true_range = max(true_range, abs(high - prev_close), abs(low - prev_close))
        ranges.append(true_range)

    ranges = ranges[-window:]
    return sum(ranges) / len(ranges)
