"""The opening-range indicator: the ORB strategy's box, drawn.

The window arithmetic itself is pinned in test_strategy_opening_range
against app.market_data.opening_range; what is new here is the wrapper --
the frame conversion, and anchoring to the session on screen rather than to
today, the same regression the premarket indicator carries next door.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.indicators import opening_range as indicator
from app.indicators.context import build_context
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


def _session_bars():
    """One finished session: premarket, two opening-window bars, the rest."""
    _, (market_open, market_close) = _recent_session()
    return [
        _bar(market_open - timedelta(hours=2), 99.0, 1.0),  # premarket spike
        _bar(market_open + timedelta(minutes=1), 10.5, 10.0),
        _bar(market_open + timedelta(minutes=3), 10.4, 9.8),
        _bar(market_open + timedelta(minutes=15), 50.0, 5.0),  # after the window
        _bar(market_close - timedelta(minutes=5), 45.0, 41.0),
    ]


def _compute(bars):
    return indicator.compute(build_context("TEST", bars, [], []))


def test_the_box_spans_the_opening_window_and_nothing_else():
    """Neither the premarket spike before the bell nor the wide 09:45 bar
    after the window may stretch the box the strategy trades."""
    assert _compute(_session_bars()) == {"High": 10.5, "Low": 9.8}


def test_the_anchor_is_the_session_on_screen_not_today():
    """On a Saturday the chart shows Friday's candles, and Friday's opening
    range is the one that belongs on them -- a wall-clock anchor drew
    nothing exactly when someone was reviewing rather than trading."""
    session_date, _ = _recent_session()
    bars = _session_bars()

    assert bars[-1].timestamp.astimezone(ET).date() == session_date
    assert _compute(bars)["High"] == 10.5


def test_a_session_with_no_opening_prints_reports_nothing():
    """None, not zero -- a zero would draw a line at the bottom of the
    chart and pull a target down to it."""
    _, (market_open, _) = _recent_session()
    bars = [_bar(market_open + timedelta(minutes=15), 50.0, 40.0)]

    assert _compute(bars) == {"High": None, "Low": None}


def test_no_bars_yields_no_levels():
    assert _compute([]) == {"High": None, "Low": None}
