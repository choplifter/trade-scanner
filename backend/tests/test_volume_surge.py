from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.market_data.volume_surge import (
    expected_window_volume,
    latest_session_bar,
    surge_ratio,
    trailing_windows,
    window_volume,
    windowed_rvol,
)
from app.services.market_clock import ET

# A real NYSE session (a Monday), so is_regular_session_bar's calendar lookup
# finds one rather than silently dropping every bar.
_DAY = date(2026, 1, 5)
_WINDOW = timedelta(minutes=60)


@dataclass
class _Bar:
    volume: float
    timestamp: datetime
    close: float = 10.0


def _bars(start: time, count: int, volume: float, day: date = _DAY) -> list[_Bar]:
    """`count` consecutive 5-minute bars from `start` ET on `day`."""
    first = datetime.combine(day, start, tzinfo=ET)
    return [_Bar(volume=volume, timestamp=first + timedelta(minutes=5 * i)) for i in range(count)]


# Flat curve: every clock time is 50% of the day done, so any window's
# expected share is zero. Used only to prove the guard; real curves rise.
_FLAT_CURVE = [(time(0, 0), 0.5)]

# A curve where each hour of the session adds a known share. Deliberately
# back-loaded like a real session: 10% by 11:00, 25% by 14:00, 45% by 15:00,
# 100% by 16:00 -- so the final hour alone is 55% of the day.
_U_CURVE = [
    (time(9, 30), 0.02),
    (time(11, 0), 0.10),
    (time(14, 0), 0.25),
    (time(15, 0), 0.45),
    (time(16, 0), 1.00),
]


def test_latest_session_bar_ignores_after_hours():
    session = _bars(time(15, 0), 2, 100.0)
    after = _bars(time(16, 30), 2, 999.0)
    assert latest_session_bar(session + after) is session[-1]


def test_latest_session_bar_is_none_outside_the_session():
    assert latest_session_bar(_bars(time(17, 0), 3, 100.0)) is None


def test_window_volume_excludes_premarket_and_other_days():
    reference = _bars(time(15, 0), 1, 100.0)[0]
    premarket = _bars(time(8, 0), 3, 500.0)
    yesterday = _bars(time(15, 0), 3, 500.0, day=date(2026, 1, 2))
    session = _bars(time(14, 30), 6, 100.0)

    total = window_volume(
        premarket + yesterday + session,
        datetime.combine(_DAY, time(14, 0), tzinfo=ET),
        datetime.combine(_DAY, time(16, 0), tzinfo=ET),
        reference,
    )
    # Only the six same-day regular-session bars count.
    assert total == 600.0


def test_recent_window_includes_the_anchor_bars_own_interval():
    # Twelve bars 15:00-15:55. The last is stamped 15:55 and covers
    # 15:55-16:00, so a 60-minute window ending at 16:00 must contain all of
    # them -- an off-by-one on the half-open edge would drop it.
    bars = _bars(time(15, 0), 12, 100.0)
    recent, _ = trailing_windows(bars, _WINDOW)
    assert recent == 1200.0


def test_trailing_windows_splits_the_two_hours():
    # 14:00-14:55 at 50/bar, then 15:00-15:55 at 100/bar.
    bars = _bars(time(14, 0), 12, 50.0) + _bars(time(15, 0), 12, 100.0)
    recent, prior = trailing_windows(bars, _WINDOW)

    assert recent == 1200.0
    assert prior == 600.0
    assert surge_ratio(recent, prior) == 2.0


def test_trailing_windows_is_none_outside_the_session():
    assert trailing_windows(_bars(time(18, 0), 12, 100.0), _WINDOW) == (None, None)


def test_surge_ratio_is_none_rather_than_infinite_on_a_dead_prior_window():
    # A ratio against zero isn't "infinitely accelerating", it's unmeasurable
    # -- and returning a huge number would put the thinnest symbols top of a
    # descending sort.
    assert surge_ratio(1000.0, 0.0) is None
    assert surge_ratio(1000.0, None) is None
    assert surge_ratio(None, 100.0) is None


def test_expected_window_volume_uses_the_share_inside_the_window():
    start = datetime.combine(_DAY, time(15, 0), tzinfo=ET)
    end = datetime.combine(_DAY, time(16, 0), tzinfo=ET)
    # 100% - 45% = 55% of a 1,000,000-share day.
    assert expected_window_volume(1_000_000.0, _U_CURVE, start, end) == 550_000.0


def test_expected_window_volume_is_none_without_a_curve_or_history():
    start = datetime.combine(_DAY, time(15, 0), tzinfo=ET)
    end = datetime.combine(_DAY, time(16, 0), tzinfo=ET)
    assert expected_window_volume(1_000_000.0, [], start, end) is None
    assert expected_window_volume(0.0, _U_CURVE, start, end) is None
    # A flat curve leaves no share inside the window -- guarded, not a
    # division by zero.
    assert expected_window_volume(1_000_000.0, _FLAT_CURVE, start, end) is None


def test_windowed_rvol_reads_as_a_multiple_of_normal():
    # Final hour normally trades 55% of 1,000,000 = 550,000. Trade exactly
    # double that and it should read 2x.
    bars = _bars(time(15, 0), 12, 1_100_000.0 / 12)
    assert round(windowed_rvol(bars, 1_000_000.0, _U_CURVE, _WINDOW), 4) == 2.0


def test_windowed_rvol_is_none_outside_the_session():
    bars = _bars(time(18, 0), 12, 100_000.0)
    assert windowed_rvol(bars, 1_000_000.0, _U_CURVE, _WINDOW) is None


def test_the_u_shape_is_exactly_what_separates_the_two_metrics():
    """The reason rvol_1h exists rather than just surge_ratio.

    This symbol is doing nothing unusual -- it trades its normal profile,
    which is back-loaded like every stock's. The self-relative ratio still
    calls that acceleration, because the last hour genuinely is busier than
    the one before it for the whole market. Only the profile-relative
    figure correctly reports "normal".
    """
    # Normal share for 14:00-15:00 is 20% and for 15:00-16:00 is 55%, so a
    # symbol tracking its profile trades 200k then 550k of a 1M-share day.
    bars = _bars(time(14, 0), 12, 200_000.0 / 12) + _bars(time(15, 0), 12, 550_000.0 / 12)

    recent, prior = trailing_windows(bars, _WINDOW)
    ratio = surge_ratio(recent, prior)
    normalized = windowed_rvol(bars, 1_000_000.0, _U_CURVE, _WINDOW)

    # Looks like a 2.75x surge...
    assert round(ratio, 2) == 2.75
    # ...but is exactly average for the time of day. Screening on the first
    # number near the close would return most of the market.
    assert round(normalized, 4) == 1.0
