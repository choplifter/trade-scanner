from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.market_data.momentum import MOMENTUM_WINDOW, pct_change_over_window


@dataclass
class _Bar:
    timestamp: datetime
    close: float


def _bars(*closes_with_offsets: tuple[int, float], base: datetime) -> list[_Bar]:
    return [_Bar(timestamp=base + timedelta(minutes=m), close=c) for m, c in closes_with_offsets]


def test_pct_change_over_window_empty_bars():
    assert pct_change_over_window([], MOMENTUM_WINDOW) is None


def test_pct_change_over_window_not_enough_history():
    base = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    bars = _bars((0, 100.0), (5, 101.0), base=base)
    assert pct_change_over_window(bars, MOMENTUM_WINDOW) is None


def test_pct_change_over_window_computes_delta():
    base = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    bars = _bars((0, 100.0), (15, 105.0), (20, 110.0), base=base)
    # Latest bar (t+20, close 110.0) vs. the closest bar at/before t+5
    # (t+0, close 100.0) -- a +10% move over the trailing 15 minutes.
    assert pct_change_over_window(bars, MOMENTUM_WINDOW) == 10.0


def test_pct_change_over_window_zero_reference_close_is_none():
    base = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    bars = _bars((0, 0.0), (20, 5.0), base=base)
    assert pct_change_over_window(bars, MOMENTUM_WINDOW) is None
