"""Slicing a strategy's signals (app.scanners.strategy_slices): the same
expectancy numbers per part of the session, per exit and per VIX band."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.scanners.strategy_slices import (
    by_exit_reason,
    by_session_part,
    by_vix_band,
    slice_stats,
    thin,
)

ET = ZoneInfo("America/New_York")


def _pick(hour, minute, r, *, day=(2026, 9, 21), reason="target", ambiguous=False):
    at = datetime(*day, hour, minute, tzinfo=ET).astimezone(timezone.utc)
    return {
        "symbol": "X",
        "timestamp": at.isoformat(),
        "r_multiple": r,
        "exit_reason": reason,
        "ambiguous_exit": ambiguous,
    }


def test_expectancy_is_the_mean_r_and_the_win_rate_comes_second():
    stats = slice_stats([_pick(10, 0, 2.0), _pick(10, 5, -1.0), _pick(10, 10, -1.0), _pick(10, 15, 3.0)])
    assert stats["trades"] == 4
    assert stats["expectancy_r"] == pytest.approx(0.75)
    assert stats["win_rate"] == 50.0
    assert stats["avg_win_r"] == pytest.approx(2.5)
    assert stats["avg_loss_r"] == pytest.approx(-1.0)


def test_an_empty_bucket_answers_with_nothing_rather_than_zero():
    """Zero expectancy and "no signals" are different findings; a bucket
    with no trades must not read as a rule that broke even."""
    assert slice_stats([])["expectancy_r"] is None
    assert slice_stats([])["trades"] == 0


def test_the_ambiguous_share_is_carried_into_every_slice():
    stats = slice_stats([_pick(10, 0, 1.0, ambiguous=True), _pick(10, 5, -1.0)])
    assert stats["ambiguous_pct"] == 50.0


def test_the_session_is_cut_where_trading_changes_not_where_it_flatters():
    picks = [_pick(9, 45, 1.0), _pick(11, 0, -1.0), _pick(13, 0, 2.0), _pick(15, 30, -1.0)]
    parts = by_session_part(picks)
    assert [p["bucket"] for p in parts][:4] == [
        "09:30-10:00 open",
        "10:00-12:00 morning",
        "12:00-14:00 midday",
        "14:00-16:00 close",
    ]
    assert [p["trades"] for p in parts] == [1, 1, 1, 1]


def test_a_part_with_no_signals_still_appears():
    parts = by_session_part([_pick(9, 45, 1.0)])
    midday = next(p for p in parts if p["bucket"].startswith("12:00"))
    assert midday["trades"] == 0 and midday["expectancy_r"] is None


def test_a_premarket_signal_is_not_silently_dropped():
    parts = by_session_part([_pick(8, 15, 1.0), _pick(10, 0, 1.0)])
    outside = next(p for p in parts if p["bucket"] == "outside the session")
    assert outside["trades"] == 1
    assert sum(p["trades"] for p in parts) == 2


def test_exits_are_grouped_by_how_the_trade_ended():
    picks = [_pick(10, 0, 2.0), _pick(10, 5, -1.0, reason="stop"), _pick(10, 10, 0.2, reason="session_close")]
    by_reason = {b["bucket"]: b for b in by_exit_reason(picks)}
    assert by_reason["target"]["trades"] == 1
    assert by_reason["stop"]["expectancy_r"] == pytest.approx(-1.0)
    assert by_reason["session_close"]["trades"] == 1


def test_vix_bands_use_the_close_of_the_day_the_signal_fired_on():
    calm = _pick(10, 0, 1.0, day=(2026, 9, 21))
    fearful = _pick(10, 0, -1.0, day=(2026, 9, 22))
    bands = {b["bucket"]: b for b in by_vix_band([calm, fearful], {date(2026, 9, 21): 14.5, date(2026, 9, 22): 28.0})}
    assert bands["VIX < 16 calm"]["trades"] == 1
    assert bands["VIX 25+ fearful"]["expectancy_r"] == pytest.approx(-1.0)
    assert bands["VIX 16-20"]["trades"] == 0


def test_a_day_without_a_vix_close_is_kept_apart_not_dropped():
    picks = [_pick(10, 0, 1.0, day=(2026, 9, 21)), _pick(10, 0, 1.0, day=(2026, 9, 22))]
    bands = by_vix_band(picks, {date(2026, 9, 21): 14.5})
    assert sum(b["trades"] for b in bands) == 2
    assert next(b for b in bands if b["bucket"] == "VIX not known")["trades"] == 1


def test_a_thin_bucket_is_marked_as_such():
    assert thin({"trades": 12}) is True
    assert thin({"trades": 400}) is False
