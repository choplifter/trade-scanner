"""The high and low of a session's first minutes.

The third kind of level in this app, and the simplest. market_data.levels
*finds* structure by counting returns; session_marks *reads* the night's and
yesterday's boundaries off the tape; this measures one fixed window at the
open. No threshold, no counting, and -- unusually for anything here -- almost
no room for interpretation: the first five minutes are the first five
minutes.

That lack of interpretation is the reason to build it. A rule stated against
this window is the rule, not a reconstruction of one, so a backtest measures
the method rather than the choices made while implementing it.

Anchored to the calendar's own opening bell rather than a literal 09:30, so a
half day is measured from its own open. Regular session only: the range is
about what the *auction* did, and a premarket print does not belong in it --
that range has its own function in session_marks.
"""

from datetime import timedelta

from app.services.market_clock import ET, trading_hours_for

# Andrew Aziz's opening range, as the user gave it. Five minutes is exactly
# one bar at the resolution everything intraday here runs at (see
# bars.MOMENTUM_BAR_MINUTES), which is a coincidence worth knowing rather
# than relying on: the functions below take the length in minutes so a
# different one does not need new code, only different data.
OPENING_MINUTES = 5


def _et(bar):
    return bar.timestamp.astimezone(ET)


def opening_range(
    bars: list, session_date, minutes: int = OPENING_MINUTES
) -> tuple[float | None, float | None]:
    """High and low of the first `minutes` of `session_date`'s regular session.

    None when the session has not opened, when the date is not a trading day,
    or when nothing traded in the window -- never zero, which would draw a
    level at the bottom of the chart and pull a target down to it.
    """
    hours = trading_hours_for(session_date)
    if hours is None:
        return None, None
    market_open = hours[0]
    window_end = market_open + timedelta(minutes=minutes)

    window = [
        bar
        for bar in bars
        if _et(bar).date() == session_date and market_open <= _et(bar) < window_end
    ]
    if not window:
        return None, None
    return max(b.high for b in window), min(b.low for b in window)


def is_complete(bar, session_date, minutes: int = OPENING_MINUTES) -> bool:
    """Whether the opening range was finished by the time `bar` closed.

    The guard that keeps a replay honest. A range measured from bars that had
    not printed yet is a range nobody could have traded against, and the
    breakout it produces would look like a signal while being a peek at the
    future. Checked against the bar being evaluated rather than against a
    clock, so it means the same thing live and in a backtest.

    A bar *inside* the window does not qualify: at 09:33 the five-minute
    range is still forming, and its high so far is not its high.
    """
    hours = trading_hours_for(session_date)
    if hours is None:
        return False
    return _et(bar) >= hours[0] + timedelta(minutes=minutes)
