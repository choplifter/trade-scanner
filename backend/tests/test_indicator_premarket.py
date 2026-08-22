"""The premarket range, and which session it belongs to.

One case here is a regression, and it is worth stating plainly because it was
invisible rather than wrong-looking. The indicator asked for *today's*
premarket. On a Saturday there is no today, so it drew nothing -- while the
chart still showed Friday's candles and a strategy targeting Friday's
premarket high pointed at a level with no line anywhere on screen. Nothing
failed; a level simply was not there.

Pinned against the real NYSE calendar rather than a frozen clock, matching
test_indicator_ranges, so these hold whenever they run.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.indicators import premarket_range
from app.indicators.context import build_context
from app.market_data import session_marks
from app.services.market_clock import ET, trading_hours_for


def _bar(timestamp, high, low):
    return SimpleNamespace(
        timestamp=timestamp,
        open=low,
        high=high,
        low=low,
        close=low,
        volume=1_000,
        vwap=None,
    )


def _recent_session():
    """The most recent date whose regular session has fully ended."""
    cursor = datetime.now(ET).date()
    for _ in range(10):
        hours = trading_hours_for(cursor)
        if hours is not None and datetime.now(ET) >= hours[1]:
            return cursor, hours
        cursor -= timedelta(days=1)
    raise AssertionError("no completed session in the last ten days")


def _session_with_premarket(pm_high, pm_low):
    """Bars for one finished session: a premarket range, then the session."""
    _, (market_open, market_close) = _recent_session()
    return [
        _bar(market_open - timedelta(hours=2), pm_high, pm_low),
        _bar(market_open - timedelta(hours=1), pm_high - 0.1, pm_low + 0.1),
        _bar(market_open + timedelta(minutes=5), 50.0, 40.0),
        _bar(market_close - timedelta(minutes=5), 45.0, 41.0),
    ]


def _compute(bars):
    return premarket_range.compute(build_context("TEST", bars, [], []))


# --- the regression -------------------------------------------------------


def test_the_premarket_of_the_session_on_screen_is_drawn():
    """The bug this file exists for. Anchored to the wall clock, this
    returned nothing outside a trading day -- so on a weekend the chart drew
    Friday's candles with no premarket line, and a strategy that had aimed at
    Friday's premarket high pointed at a level the reader could not see."""
    bars = _session_with_premarket(12.0, 11.0)

    assert _compute(bars) == {"High": 12.0, "Low": 11.0}


def test_the_anchor_is_the_last_bar_not_today():
    """Stated directly, because "today" is right on most days and wrong on
    exactly the days someone is reviewing rather than trading."""
    bars = _session_with_premarket(12.0, 11.0)

    assert session_marks.latest_session_date(bars) == bars[-1].timestamp.astimezone(ET).date()


# --- what it measures -----------------------------------------------------


def test_regular_session_bars_are_excluded():
    """Otherwise the "premarket" range is just the day's range."""
    bars = _session_with_premarket(12.0, 11.0)

    result = _compute(bars)

    assert result["High"] == 12.0  # not the 50.0 that traded after the bell
    assert result["Low"] == 11.0


def test_the_range_spans_every_premarket_bar():
    bars = _session_with_premarket(12.0, 11.0)

    assert _compute(bars) == {"High": 12.0, "Low": 11.0}


def test_a_session_with_no_premarket_prints_reports_nothing():
    """Thin names simply do not trade before the bell. None, not zero -- a
    zero would draw a line at the bottom of the chart."""
    _, (market_open, _) = _recent_session()
    bars = [_bar(market_open + timedelta(minutes=5), 50.0, 40.0)]

    assert _compute(bars) == {"High": None, "Low": None}


def test_no_bars_yields_no_levels():
    assert _compute([]) == {"High": None, "Low": None}


def test_an_earlier_session_does_not_leak_into_the_latest_one():
    """Two days of bars, and only the newer day's premarket counts -- the
    slice is by session, not by "everything before the bell"."""
    _, (market_open, _) = _recent_session()
    older = market_open - timedelta(days=7)
    bars = [
        _bar(older - timedelta(hours=2), 99.0, 98.0),
        _bar(older + timedelta(minutes=5), 97.0, 96.0),
        _bar(market_open - timedelta(hours=2), 12.0, 11.0),
        _bar(market_open + timedelta(minutes=5), 50.0, 40.0),
    ]

    assert _compute(bars) == {"High": 12.0, "Low": 11.0}
