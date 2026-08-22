"""The prior session's close as a mark of its own.

The range bounds are covered through the indicators that draw them
(test_indicator_ranges, test_indicator_premarket); the close is newer and is
what the strategies now aim at, so its edges are pinned here directly: which
bar's close it is, and that an absent one is omitted from the marks rather
than reported as a zero a target could be dragged down to.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.market_data import session_marks
from app.services.market_clock import trading_hours_for

# 2026-08-19 is a Wednesday with a full prior session on the 18th.
_DAY = date(2026, 8, 19)
_PRIOR = date(2026, 8, 18)
_PRIOR_OPEN, _PRIOR_CLOSE_TS = trading_hours_for(_PRIOR)


@dataclass
class _Bar:
    timestamp: datetime
    high: float
    low: float
    close: float


def _prior_bar(minutes_after_open, high, low, close):
    return _Bar(_PRIOR_OPEN + timedelta(minutes=minutes_after_open), high, low, close)


def test_the_close_is_the_last_regular_bar_by_timestamp():
    """By timestamp, not list position, so an unsorted series cannot report
    a mid-afternoon print as the close."""
    bars = [
        _prior_bar(300, 12.0, 11.0, 11.5),  # the actual last regular bar
        _prior_bar(1, 10.5, 10.0, 10.2),
    ]

    assert session_marks.prior_session_close(bars, _DAY) == 11.5


def test_an_afterhours_print_is_not_the_close():
    bars = [
        _prior_bar(300, 12.0, 11.0, 11.5),
        _Bar(_PRIOR_CLOSE_TS + timedelta(hours=2), 9.0, 8.0, 8.5),
    ]

    assert session_marks.prior_session_close(bars, _DAY) == 11.5


def test_no_prior_session_reports_none_not_zero():
    assert session_marks.prior_session_close([], _DAY) is None


def test_the_close_is_one_of_the_session_marks():
    bars = [_prior_bar(1, 12.0, 10.0, 11.5)]

    assert 11.5 in session_marks.marks_for_session(bars, _DAY)
