"""The day- and week-scale reference levels.

Both indicators answer "the last *completed* period", which is the part that
is easy to get wrong: skipping the final bar unconditionally breaks once that
period has genuinely ended, and not skipping it reports a half-finished
period as though it were settled. Each case is pinned here against the real
NYSE calendar rather than a frozen clock, so the tests hold whenever they run.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from app.indicators import daily_range, weekly_range
from app.indicators.context import build_context
from app.services.market_clock import ET, trading_hours_for


def _bar(timestamp, high, low, close=None):
    return SimpleNamespace(
        timestamp=timestamp,
        open=low,
        high=high,
        low=low,
        close=close if close is not None else low,
        volume=1_000,
        vwap=None,
    )


def _closed_sessions(count):
    """The `count` most recent sessions whose regular hours have fully ended."""
    now = datetime.now(ET)
    days, cursor = [], now.date()
    while len(days) < count and (now.date() - cursor).days < 30:
        hours = trading_hours_for(cursor)
        if hours is not None and hours[1] <= now:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _next_session_after(day):
    cursor = day + timedelta(days=1)
    while trading_hours_for(cursor) is None:
        cursor += timedelta(days=1)
    return cursor


def _ctx_with_sessions(levels):
    """levels: {date: (high, low)} -> a context whose minute bars sit at each
    session's open, inside regular hours."""
    bars = []
    for day, (high, low) in levels.items():
        market_open = trading_hours_for(day)[0]
        bars.append(_bar(market_open + timedelta(minutes=1), high, low))
    return build_context("TEST", bars, [], [])


# --- daily range --------------------------------------------------------


def test_no_bars_yields_no_levels():
    assert daily_range.compute(build_context("TEST", [], [], [])) == {"High": None, "Low": None}


def test_picks_the_most_recently_completed_session():
    older, latest = _closed_sessions(2)
    ctx = _ctx_with_sessions({older: (10.0, 9.0), latest: (20.0, 19.0)})

    assert daily_range.compute(ctx) == {"High": 20.0, "Low": 19.0}


def test_a_session_still_in_progress_is_excluded():
    """The whole point of the elapsed check. A session whose close has not
    passed yet must not be reported as the prior day's settled range."""
    latest = _closed_sessions(1)[0]
    upcoming = _next_session_after(latest)
    ctx = _ctx_with_sessions({latest: (20.0, 19.0), upcoming: (99.0, 1.0)})

    # The in-progress session's wild levels must not win.
    assert daily_range.compute(ctx) == {"High": 20.0, "Low": 19.0}


def test_extended_hours_prints_are_excluded():
    """Premarket and afterhours are thin enough that one stray tick would
    otherwise set a high no meaningful volume traded at -- and today's
    premarket already has its own indicator."""
    latest = _closed_sessions(1)[0]
    market_open, market_close = trading_hours_for(latest)
    bars = [
        _bar(market_open - timedelta(hours=2), 500.0, 400.0),   # premarket spike
        _bar(market_open + timedelta(minutes=1), 20.0, 19.0),   # the real session
        _bar(market_close + timedelta(hours=1), 0.5, 0.1),      # afterhours air pocket
    ]

    assert daily_range.compute(build_context("TEST", bars, [], [])) == {"High": 20.0, "Low": 19.0}


def test_the_high_and_low_span_the_whole_session():
    latest = _closed_sessions(1)[0]
    market_open = trading_hours_for(latest)[0]
    bars = [
        _bar(market_open + timedelta(minutes=1), 15.0, 14.0),
        _bar(market_open + timedelta(hours=2), 22.0, 13.0),
        _bar(market_open + timedelta(hours=4), 18.0, 17.0),
    ]

    assert daily_range.compute(build_context("TEST", bars, [], [])) == {"High": 22.0, "Low": 13.0}


def test_bars_on_a_non_trading_day_are_ignored():
    """Weekend/holiday timestamps have no regular session to measure, so they
    must not become a 'prior day' of their own."""
    latest = _closed_sessions(1)[0]
    market_open = trading_hours_for(latest)[0]
    saturday = market_open + timedelta(days=(5 - market_open.weekday()) % 7 or 7)
    assert trading_hours_for(saturday.date()) is None

    bars = [_bar(market_open + timedelta(minutes=1), 20.0, 19.0), _bar(saturday, 77.0, 66.0)]

    assert daily_range.compute(build_context("TEST", bars, [], [])) == {"High": 20.0, "Low": 19.0}


# --- weekly range, including the close ----------------------------------


def _weekly_ctx(rows):
    """rows: list of (week_start, high, low, close)."""
    # Already tz-aware, so construct straight from it -- passing tz= as well
    # is what pandas rejects.
    bars = [_bar(pd.Timestamp(start), high, low, close) for start, high, low, close in rows]
    return build_context("TEST", [], bars, [])


def test_weekly_close_comes_from_the_prior_completed_week():
    now = datetime.now(timezone.utc)
    finished = now - timedelta(days=14)
    running = now - timedelta(days=2)
    ctx = _weekly_ctx([(finished, 30.0, 20.0, 25.0), (running, 99.0, 1.0, 50.0)])

    assert weekly_range.compute(ctx) == {"High": 30.0, "Low": 20.0, "Close": 25.0}


def test_weekly_close_is_none_without_a_completed_week():
    ctx = _weekly_ctx([(datetime.now(timezone.utc) - timedelta(days=1), 30.0, 20.0, 25.0)])

    assert weekly_range.compute(ctx) == {"High": None, "Low": None, "Close": None}


def test_weekly_range_reports_close_alongside_the_bounds():
    """The close is a different kind of level from the range bounds -- where
    price was accepted, not where it reached -- so it is reported as its own
    entry rather than folded into either."""
    ctx = _weekly_ctx([(datetime.now(timezone.utc) - timedelta(days=14), 30.0, 20.0, 21.5)])
    result = weekly_range.compute(ctx)

    assert set(result) == {"High", "Low", "Close"}
    assert result["Close"] == 21.5
    assert "Close" in weekly_range.COLORS
