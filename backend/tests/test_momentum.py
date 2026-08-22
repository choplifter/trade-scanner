from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.market_data.momentum import MOMENTUM_WINDOW, pct_change_over_window


@dataclass
class _Bar:
    timestamp: datetime
    close: float


def _bars(*closes_with_offsets: tuple[int, float], base: datetime) -> list[_Bar]:
    return [_Bar(timestamp=base + timedelta(minutes=m), close=c) for m, c in closes_with_offsets]


# Offsets below are expressed against the window rather than written out, so
# these keep testing what they claim when MOMENTUM_WINDOW changes. Written as
# literals they did not fail loudly -- one of them kept passing because the
# reference bar had fallen off the front, which is a different bug entirely.
_WINDOW_MINUTES = int(MOMENTUM_WINDOW.total_seconds() // 60)


def test_pct_change_over_window_empty_bars():
    assert pct_change_over_window([], MOMENTUM_WINDOW) is None


def test_pct_change_over_window_not_enough_history():
    base = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    bars = _bars((0, 100.0), (5, 101.0), base=base)
    assert pct_change_over_window(bars, MOMENTUM_WINDOW) is None


def test_pct_change_over_window_computes_delta():
    base = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    bars = _bars(
        (0, 100.0), (_WINDOW_MINUTES, 105.0), (_WINDOW_MINUTES + 5, 110.0), base=base
    )
    # The latest bar sits one window plus five minutes after the first, so the
    # lookback lands on the closest bar at/before t+5 -- the 100.0 one at t+0.
    assert pct_change_over_window(bars, MOMENTUM_WINDOW) == 10.0


def test_pct_change_over_window_zero_reference_close_is_none():
    """A reference that exists but is zero -- distinct from having no
    reference at all, which the not-enough-history case above covers."""
    base = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    bars = _bars((0, 0.0), (_WINDOW_MINUTES, 5.0), base=base)
    assert pct_change_over_window(bars, MOMENTUM_WINDOW) is None
