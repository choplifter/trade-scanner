"""prior_completed_period must judge "is this period over" against the
caller's own reference time, not real wall-clock now -- otherwise a replay
of a past date would report the *actual* current week/month as complete
even while the replayed session is still mid-week. See
app.indicators.context.IndicatorContext.as_of and its callers in
weekly_range.py / monthly_range.py.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.indicators import weekly_range
from app.indicators.context import build_context


def _bar(timestamp, high, low, close):
    return SimpleNamespace(timestamp=timestamp, open=low, high=high, low=low, close=close, volume=1_000, vwap=None)


def test_weekly_range_uses_as_of_not_real_now():
    real_now = datetime.now(timezone.utc)
    week_start = real_now - timedelta(days=10)

    # As of real "now" this week is long over (it started 10 days ago), so
    # without the fix this would report it as the prior completed week --
    # but the replay being tested is only 2 days into that same week, well
    # before it actually ended.
    replay_as_of = week_start + timedelta(days=2)
    bars = [_bar(week_start, 30.0, 20.0, 25.0)]
    ctx = build_context("TEST", [], bars, [], as_of=replay_as_of)

    assert weekly_range.compute(ctx) == {"High": None, "Low": None, "Close": None}


def test_weekly_range_reports_it_once_as_of_reaches_the_periods_end():
    week_start = datetime.now(timezone.utc) - timedelta(days=10)
    replay_as_of = week_start + timedelta(days=8)  # past the 7-day period end
    bars = [_bar(week_start, 30.0, 20.0, 25.0)]
    ctx = build_context("TEST", [], bars, [], as_of=replay_as_of)

    assert weekly_range.compute(ctx) == {"High": 30.0, "Low": 20.0, "Close": 25.0}


def test_as_of_defaults_to_real_now_for_live_callers():
    """Every existing live call site builds a context without an `as_of`
    kwarg -- confirms that path is unaffected by the fix."""
    week_start = datetime.now(timezone.utc) - timedelta(days=14)
    bars = [_bar(week_start, 30.0, 20.0, 25.0)]
    ctx = build_context("TEST", [], bars, [])

    assert weekly_range.compute(ctx) == {"High": 30.0, "Low": 20.0, "Close": 25.0}
