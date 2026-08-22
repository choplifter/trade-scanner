"""The daily ATR the opening-range rule vetoes against.

The risk worth pinning is the same one session_marks carries: reading it
from bars that are not the prior completed regular sessions -- today's own
prints, premarket ticks, a half-finished average from two days of history.
The arithmetic (true range with the gap term) gets one case each.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from app.market_data import atr
from app.services.market_clock import trading_hours_for

# A run of full sessions ending Friday 2026-08-21, walked backwards from a
# fixed date rather than the wall clock so the tests hold whenever they run.
_SESSION_DATE = date(2026, 8, 22)


def _prior_sessions(count):
    days, cursor = [], _SESSION_DATE - timedelta(days=1)
    while len(days) < count:
        if trading_hours_for(cursor) is not None:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


@dataclass
class _Bar:
    timestamp: datetime
    high: float
    low: float
    close: float


def _session_bar(day, high, low, close, minutes_after_open=1):
    open_ = trading_hours_for(day)[0]
    return _Bar(open_ + timedelta(minutes=minutes_after_open), high, low, close)


def _flat_days(count, high=11.0, low=10.0, close=10.5):
    return [_session_bar(day, high, low, close) for day in _prior_sessions(count)]


def test_the_average_of_identical_days_is_their_range():
    assert atr.daily_atr(_flat_days(atr.MIN_SESSIONS), _SESSION_DATE) == pytest.approx(1.0)


def test_a_gap_widens_the_range_to_the_prior_close():
    """True range, not high minus low: day two gaps to 20-21 after a 10.5
    close, so its travel is 21 - 10.5, not the bar's own 1.0."""
    days = _prior_sessions(atr.MIN_SESSIONS)
    bars = [_session_bar(day, 11.0, 10.0, 10.5) for day in days[:-1]]
    bars.append(_session_bar(days[-1], 21.0, 20.0, 20.5))

    expected = ((atr.MIN_SESSIONS - 1) * 1.0 + (21.0 - 10.5)) / atr.MIN_SESSIONS
    assert atr.daily_atr(bars, _SESSION_DATE) == pytest.approx(expected)


def test_too_few_sessions_is_unknowable_not_zero():
    assert atr.daily_atr(_flat_days(atr.MIN_SESSIONS - 1), _SESSION_DATE) is None


def test_the_session_being_traded_does_not_count():
    """The look-ahead guard: replaying a Friday must not read Friday."""
    friday = _prior_sessions(1)[0]
    bars = _flat_days(atr.MIN_SESSIONS - 1, high=11.0, low=10.0)
    bars.append(_session_bar(friday, 99.0, 1.0, 50.0))

    assert atr.daily_atr(bars, friday) is None


def test_extended_hours_prints_are_excluded():
    """One thin premarket tick must not set a daily high nothing traded at."""
    bars = _flat_days(atr.MIN_SESSIONS)
    spike_day = _prior_sessions(1)[0]
    premarket = trading_hours_for(spike_day)[0] - timedelta(hours=2)
    bars.append(_Bar(premarket, 500.0, 400.0, 450.0))

    assert atr.daily_atr(bars, _SESSION_DATE) == pytest.approx(1.0)


def test_only_the_window_of_most_recent_sessions_counts():
    """A wild day older than the window has aged out of 'typical'. Its
    close is kept ordinary so only the range ages out here -- the close
    would legitimately reach in through the gap term of the window's first
    day, which is the ATR convention, not a leak."""
    days = _prior_sessions(atr.WINDOW_SESSIONS + 1)
    bars = [_session_bar(days[0], 99.0, 1.0, 10.5)]
    bars += [_session_bar(day, 11.0, 10.0, 10.5) for day in days[1:]]

    assert atr.daily_atr(bars, _SESSION_DATE) == pytest.approx(1.0)
