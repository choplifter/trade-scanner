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

from app.indicators import daily_range, market_structure, weekly_range
from app.indicators.context import TIMEFRAME_ORDER, build_context, timeframe_rank
from app.indicators import loader
from app.indicators.loader import DASH_PATTERNS, run_indicators


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
        "Market Structure",
        "Strategy Signal",
        "Strategy Entry",
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


def test_the_entry_marker_shares_the_signal_ceiling():
    """They describe one trade, so one appearing without the other would be a
    stop and a target with no entry, or an arrow with nothing to aim at."""
    for timeframe in ("1Min", "1Hour", "1Day", "1Week"):
        names = _names_at(timeframe)
        assert ("Strategy Entry" in names) == ("Strategy Signal" in names), timeframe


def test_the_strategy_signal_stops_at_the_hourly_chart():
    """It draws three prices from one intraday setup. Above the hourly chart
    those would be pinned to the right-hand edge of a multi-year view, which
    is the failure market_structure already had once."""
    assert "Strategy Signal" in _names_at("1Hour")
    assert "Strategy Signal" not in _names_at("1Day")


def test_the_weekly_range_survives_up_to_the_weekly_chart():
    assert "Weekly Range" in _names_at("1Day")
    assert "Weekly Range" in _names_at("1Week")
    assert "Weekly Range" not in _names_at("1Month")


def test_market_structure_is_drawn_up_to_its_anchor():
    """Anchored on hourly bars and shown on the charts traded from -- the
    levels have to be identical on 1m, 5m/15m and 1h or the workflow of
    marking structure on the hourly chart and dropping down to enter is
    broken. Above the anchor they would be sixty days of hourly structure
    pinned to the edge of a multi-year chart."""
    assert "Market Structure" in _names_at("1Min")
    assert "Market Structure" in _names_at("1Hour")
    assert "Market Structure" not in _names_at("4Hour")
    assert "Market Structure" not in _names_at("1Day")
    assert "Market Structure" not in _names_at("1Month")


def test_the_premarket_range_drops_above_the_daily_chart():
    """A fraction of one session, so a daily candle already contains it whole."""
    assert "Premarket Range" in _names_at("1Day")
    assert "Premarket Range" not in _names_at("1Week")


def test_the_monthly_chart_keeps_only_the_monthly_range():
    """Of the range levels, that is -- the ungated indicators stay throughout."""
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


# --- how each indicator says it should be drawn -------------------------


def _results_at(timeframe="1Min"):
    return run_indicators(build_context("TEST", [], [], [], timeframe))


def test_every_indicator_reports_a_style():
    """Present even when the file declares nothing, so the client always has
    the key and falls back on its own defaults rather than on undefined."""
    for result in _results_at():
        assert "style" in result, result["name"]
        assert isinstance(result["style"], dict)


def test_a_declared_style_reaches_the_client():
    """Checks the pass-through, deliberately not the specific look: which
    dash an indicator uses is a preference its file owns, and a test that
    pins one turns every restyling into a failing build."""
    styles = {r["name"]: r["style"] for r in _results_at()}
    declared = {
        "Market Structure": market_structure.STYLE,
        "Daily Range": daily_range.STYLE,
        "Weekly Range": weekly_range.STYLE,
    }

    for name, expected in declared.items():
        assert styles[name] == expected, name


def test_an_indicator_without_a_style_gets_an_empty_one():
    """VWAP and the EMAs declare none, so they keep the client's default for
    a series -- exactly what they rendered as before STYLE existed."""
    styles = {r["name"]: r["style"] for r in _results_at()}

    assert styles["VWAP"] == {}
    assert styles["EMA"] == {}


def test_declared_dash_patterns_are_spelled_correctly():
    """A typo would not raise anywhere -- the client falls back to its
    default line -- so the indicator would just quietly stop looking the way
    its author wrote down."""
    for result in _results_at():
        dash = result["style"].get("dash")
        assert dash is None or dash in DASH_PATTERNS, f"{result['name']}: {dash!r}"


def test_declared_widths_are_drawable():
    """lightweight-charts takes 1-4; anything else is silently ignored."""
    for result in _results_at():
        width = result["style"].get("width")
        assert width is None or width in (1, 2, 3, 4), f"{result['name']}: {width!r}"


# --- a broken indicator has to be visible --------------------------------


def test_a_failing_indicator_is_reported_rather_than_dropped(tmp_path, monkeypatch):
    """The failure this closes. An indicator that raises used to be skipped,
    and a skipped indicator draws nothing -- which on a chart is
    indistinguishable from one that found nothing, so a broken file reads as
    a quiet market.

    Observed for real: indicator files are re-executed per request while the
    modules they import are not, so editing a shared module leaves the two out
    of step until a restart. market_structure called a signature that did not
    exist yet, raised, and vanished from the chart with no sign of why.
    """
    (tmp_path / "broken_thing.py").write_text(
        'NAME = "Broken"\nKIND = "level"\n\n\ndef compute(ctx):\n    raise ValueError("boom")\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "_DIR", tmp_path)

    results = run_indicators(build_context("TEST", [], [], []))

    assert len(results) == 1
    assert results[0]["kind"] == loader.KIND_ERROR
    assert "ValueError" in results[0]["error"]
    assert "boom" in results[0]["error"]
    assert results[0]["series"] == {}


def test_a_file_that_cannot_even_be_imported_is_reported(tmp_path, monkeypatch):
    """It has no NAME to report itself by, so the filename has to do."""
    (tmp_path / "syntax_trouble.py").write_text("this is not python\n", encoding="utf-8")
    monkeypatch.setattr(loader, "_DIR", tmp_path)

    results = run_indicators(build_context("TEST", [], [], []))

    assert len(results) == 1
    assert results[0]["name"] == "Syntax Trouble"
    assert results[0]["kind"] == loader.KIND_ERROR


def test_one_broken_file_does_not_cost_the_working_ones(tmp_path, monkeypatch):
    (tmp_path / "broken.py").write_text(
        'NAME = "Broken"\nKIND = "level"\n\n\ndef compute(ctx):\n    raise ValueError("boom")\n',
        encoding="utf-8",
    )
    (tmp_path / "fine.py").write_text(
        'NAME = "Fine"\nKIND = "level"\n\n\ndef compute(ctx):\n    return {"High": 1.0}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "_DIR", tmp_path)

    results = {r["name"]: r for r in run_indicators(build_context("TEST", [], [], []))}

    assert results["Fine"]["series"] == {"High": 1.0}
    assert results["Broken"]["kind"] == loader.KIND_ERROR
