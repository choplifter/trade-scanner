from datetime import datetime, time, timezone
from types import SimpleNamespace

from app.market_data.volume_profile import (
    MIN_FRACTION,
    build_fraction_curve,
    fraction_at,
)
from app.services.market_clock import ET


def _bar(et_naive: datetime, volume: float):
    """A 5m bar stamped in UTC, the way Alpaca returns them."""
    return SimpleNamespace(
        timestamp=et_naive.replace(tzinfo=ET).astimezone(timezone.utc), volume=volume
    )


def _day(date_str: str, volumes: dict[str, float]) -> list:
    return [
        _bar(datetime.fromisoformat(f"{date_str}T{hhmm}"), volume)
        for hhmm, volume in volumes.items()
    ]


def test_build_fraction_curve_is_cumulative_and_ends_at_one():
    bars = _day("2026-08-12", {"09:30": 50.0, "09:35": 30.0, "09:40": 20.0})
    curve = build_fraction_curve(bars)
    assert [bucket for bucket, _ in curve] == [time(9, 30), time(9, 35), time(9, 40)]
    assert [round(f, 4) for _, f in curve] == [0.5, 0.8, 1.0]


def test_build_fraction_curve_averages_across_days_rather_than_summing_volume():
    # Day two is 100x heavier but has the same *shape*. Averaging per-bucket
    # fractions keeps the shape; summing raw volume first would let day two
    # dominate (here the shape matches, so the result must be identical to
    # either day alone).
    bars = _day("2026-08-12", {"09:30": 50.0, "09:35": 50.0}) + _day(
        "2026-08-13", {"09:30": 5000.0, "09:35": 5000.0}
    )
    curve = build_fraction_curve(bars)
    assert [round(f, 4) for _, f in curve] == [0.5, 1.0]


def test_build_fraction_curve_is_monotonic_with_uneven_day_coverage():
    # Day two skips the 09:35 bucket entirely (a half-day, or no trade). The
    # per-bucket averages could otherwise dip; a cumulative curve must not.
    bars = _day("2026-08-12", {"09:30": 10.0, "09:35": 40.0, "09:40": 50.0}) + _day(
        "2026-08-13", {"09:30": 90.0, "09:40": 10.0}
    )
    curve = build_fraction_curve(bars)
    fractions = [f for _, f in curve]
    assert fractions == sorted(fractions), f"curve must never decrease: {fractions}"
    assert fractions[-1] <= 1.0


def test_build_fraction_curve_ignores_a_zero_volume_day():
    bars = _day("2026-08-12", {"09:30": 100.0}) + _day("2026-08-13", {"09:30": 0.0})
    assert build_fraction_curve(bars) == [(time(9, 30), 1.0)]


def test_build_fraction_curve_empty_input():
    assert build_fraction_curve([]) == []


def _curve():
    return [(time(9, 30), 0.2), (time(12, 0), 0.6), (time(15, 55), 1.0)]


def test_fraction_at_uses_the_latest_bucket_at_or_before_now():
    at_1pm = datetime(2026, 8, 12, 13, 0, tzinfo=ET)
    assert fraction_at(_curve(), at_1pm) == 0.6


def test_fraction_at_before_the_first_bucket_is_the_floor():
    at_5am = datetime(2026, 8, 12, 5, 0, tzinfo=ET)
    assert fraction_at(_curve(), at_5am) == MIN_FRACTION


def test_fraction_at_after_the_close_is_one():
    # Once the session is over the un-normalized full-day comparison is the
    # correct one, so the fraction has to reach exactly 1.0.
    after_close = datetime(2026, 8, 12, 18, 0, tzinfo=ET)
    assert fraction_at(_curve(), after_close) == 1.0


def test_fraction_at_with_no_curve_falls_back_to_one():
    # The safe default: RVOL behaves exactly as it did before normalization
    # existed, rather than producing something wrong.
    assert fraction_at([], datetime(2026, 8, 12, 10, 0, tzinfo=ET)) == 1.0


def test_fraction_at_converts_from_utc():
    # 14:00 UTC is 10:00 ET in August (EDT) -- after the 09:30 bucket, before
    # noon. Getting the timezone wrong here would silently pick 15:55's 1.0.
    at_10am_et = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
    assert fraction_at(_curve(), at_10am_et) == 0.2


def test_engine_gate_returns_none_when_normalization_is_disabled():
    """The setting is what decides whether RVOL gets normalized at all.

    Off by default, because formulas._FADE_RISK_RVOL is calibrated against the
    un-normalized definition -- so a default-constructed engine must leave RVOL
    exactly as it was.
    """
    from types import SimpleNamespace

    from app.scanners.engine import ScannerEngine

    engine = object.__new__(ScannerEngine)
    engine.settings = SimpleNamespace(scanner_rvol_time_normalized=False)
    engine.volume_profile = SimpleNamespace(fraction_at=lambda _at: 0.05)
    assert engine._session_volume_fraction(datetime(2026, 8, 12, 9, 35, tzinfo=ET)) is None

    engine.settings = SimpleNamespace(scanner_rvol_time_normalized=True)
    assert engine._session_volume_fraction(datetime(2026, 8, 12, 9, 35, tzinfo=ET)) == 0.05
