"""Which indicators are offered at which chart resolution.

An indicator describing one period is noise once a single candle covers that
period: yesterday's high sits inside one bar of a weekly chart, so the line
can never be seen being crossed. Each indicator declares a ceiling and the
loader drops the rest.

These run the real loader over the real indicator directory rather than
stubs, so adding a file without a ceiling -- or mistyping one -- shows up
here instead of on the chart.
"""

import pytest

from app.indicators.context import TIMEFRAME_ORDER, build_context, timeframe_rank
from app.indicators.loader import run_indicators


def _names_at(timeframe):
    ctx = build_context("TEST", [], [], [], timeframe)
    return {ind["name"] for ind in run_indicators(ctx)}


# --- the ordering -------------------------------------------------------


def test_timeframes_run_finest_to_coarsest():
    ranks = [timeframe_rank(tf) for tf in TIMEFRAME_ORDER]

    assert ranks == sorted(ranks)
    assert timeframe_rank("1Min") < timeframe_rank("1Day") < timeframe_rank("1Month")


def test_an_unknown_timeframe_hides_nothing():
    """Fail open. A new chart resolution should show every indicator until
    someone decides otherwise -- blanking the whole panel would look like a
    broken backend rather than a deliberate filter."""
    assert timeframe_rank("3Min") == 0
    assert _names_at("7Year") == _names_at("1Min")


# --- what survives each timeframe ---------------------------------------


def test_every_indicator_is_offered_on_the_minute_chart():
    """Also pins that none of them raise on an empty context -- run_indicators
    swallows exceptions, so a crashing indicator would otherwise just quietly
    vanish."""
    assert _names_at("1Min") == {
        "VWAP",
        "EMA",
        "Premarket Range",
        "Daily Range",
        "Weekly Range",
        "Monthly Range",
    }


def test_the_overlays_are_deliberately_not_gated():
    """VWAP and the EMAs declare no ceiling and so are offered everywhere.

    Not an oversight: the gating was added for the range levels only, and
    these two were explicitly left as they were. Pinned so that a later
    tidy-up pass giving every indicator a ceiling "for consistency" has to
    argue with a test first.
    """
    for timeframe in TIMEFRAME_ORDER:
        names = _names_at(timeframe)
        assert "VWAP" in names, f"VWAP disappeared at {timeframe}"
        assert "EMA" in names, f"EMA disappeared at {timeframe}"


def test_the_hourly_chart_still_offers_the_daily_range():
    assert "Daily Range" in _names_at("1Hour")


def test_the_daily_range_survives_up_to_the_daily_chart():
    assert "Daily Range" in _names_at("1Day")
    assert "Daily Range" not in _names_at("1Week")
    assert "Daily Range" not in _names_at("1Month")


def test_the_weekly_range_survives_up_to_the_weekly_chart():
    assert "Weekly Range" in _names_at("1Day")
    assert "Weekly Range" in _names_at("1Week")
    assert "Weekly Range" not in _names_at("1Month")


def test_the_premarket_range_drops_above_the_daily_chart():
    """A fraction of one session, so a daily candle already contains it whole."""
    assert "Premarket Range" in _names_at("1Day")
    assert "Premarket Range" not in _names_at("1Week")


def test_the_monthly_chart_keeps_only_the_monthly_range():
    """Of the range levels, that is -- the ungated overlays stay throughout."""
    assert _names_at("1Month") == {"Monthly Range", "VWAP", "EMA"}


@pytest.mark.parametrize("timeframe", TIMEFRAME_ORDER)
def test_no_timeframe_is_left_with_an_empty_panel(timeframe):
    assert _names_at(timeframe), f"{timeframe} would render no indicators at all"


def test_the_offered_set_only_ever_shrinks_as_the_chart_coarsens():
    """The ceilings have to be monotonic: an indicator that reappears at a
    coarser timeframe would mean two ceilings disagree."""
    previous = None
    for timeframe in TIMEFRAME_ORDER:
        names = _names_at(timeframe)
        if previous is not None:
            assert names <= previous, f"{timeframe} added indicators back: {names - previous}"
        previous = names
