from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.market_data.bars import aggregate_last_n_minutes


@dataclass
class _Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime


_BASE = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)


def _bar(minute: int, open_: float, high: float, low: float, close: float, volume: float = 1000.0) -> _Bar:
    return _Bar(open=open_, high=high, low=low, close=close, volume=volume, timestamp=_BASE + timedelta(minutes=minute))


def test_aggregate_last_n_minutes_empty_bars_returns_none():
    assert aggregate_last_n_minutes([], 5) is None


def test_aggregate_last_n_minutes_combines_bars_in_latest_bucket():
    # Bucket boundaries are clock-aligned at :00/:05/:10/... -- the latest
    # bar is at minute 33, so the bucket spans minutes 30-33 (4 bars);
    # minutes 28 and 29 belong to the *previous* bucket and must be
    # excluded from the aggregate.
    bars = [
        _bar(28, open_=50.0, high=51.0, low=49.0, close=50.5, volume=100.0),
        _bar(29, open_=50.5, high=52.0, low=50.0, close=51.5, volume=100.0),
        _bar(30, open_=100.0, high=101.0, low=99.0, close=100.5, volume=200.0),
        _bar(31, open_=100.5, high=103.0, low=100.0, close=102.0, volume=200.0),
        _bar(32, open_=102.0, high=102.5, low=95.0, close=96.0, volume=200.0),
        _bar(33, open_=96.0, high=98.0, low=95.5, close=97.5, volume=200.0),
    ]

    candle = aggregate_last_n_minutes(bars, 5)

    assert candle.open == 100.0  # minute 30 -- first bar in the 30-33 bucket
    assert candle.high == 103.0  # minute 31's high
    assert candle.low == 95.0  # minute 32's low
    assert candle.close == 97.5  # minute 33 -- last bar in the bucket
    assert candle.volume == 800.0  # 200 x the 4 bars actually in the bucket


def test_aggregate_last_n_minutes_single_bar_bucket_when_latest_is_on_a_boundary():
    # The latest bar's own minute (30) is itself a bucket boundary, so
    # nothing before it belongs to the same bucket -- degenerates to just
    # that one bar's own OHLCV, unchanged.
    bars = [
        _bar(28, open_=50.0, high=51.0, low=49.0, close=50.5, volume=100.0),
        _bar(29, open_=50.5, high=52.0, low=50.0, close=51.5, volume=100.0),
        _bar(30, open_=100.0, high=101.0, low=99.0, close=100.5, volume=200.0),
    ]

    candle = aggregate_last_n_minutes(bars, 5)

    assert candle.open == 100.0
    assert candle.high == 101.0
    assert candle.low == 99.0
    assert candle.close == 100.5
    assert candle.volume == 200.0


def test_aggregate_last_n_minutes_ignores_bars_far_before_the_window():
    # A single stray bar hours earlier shouldn't leak into the aggregate --
    # only the trailing `minutes` count is ever considered.
    bars = [_bar(0, open_=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)] + [
        _bar(30 + i, open_=10.0, high=10.0, low=10.0, close=10.0, volume=10.0) for i in range(5)
    ]

    candle = aggregate_last_n_minutes(bars, 5)

    assert candle.open == 10.0
    assert candle.high == 10.0
    assert candle.low == 10.0
