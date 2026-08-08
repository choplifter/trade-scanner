from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.scanners import seasonality as seasonality_module
from app.scanners.seasonality import compute_seasonality


@dataclass
class FakeBar:
    timestamp: datetime
    close: float


def _bar(day: str, close: float) -> FakeBar:
    # Real Alpaca daily bars are timestamped 04:00 UTC (= midnight ET,
    # confirmed against a live fetch) -- match that convention here so the
    # astimezone(ET).date() call in compute_seasonality resolves to the
    # intended trading date instead of rolling back a day.
    ts = datetime.fromisoformat(day).replace(hour=4, tzinfo=timezone.utc)
    return FakeBar(timestamp=ts, close=close)


# Two consecutive weeks of daily closes. Monday->Tuesday is always +10%,
# every other transition is flat, so the "Tuesday" bucket should show a
# 100% win rate and a positive mean while other weekdays show 0%/non-positive.
_BARS = [
    _bar("2026-08-03", 100.0),  # Monday
    _bar("2026-08-04", 110.0),  # Tuesday: +10%
    _bar("2026-08-05", 110.0),  # Wednesday: 0%
    _bar("2026-08-06", 110.0),  # Thursday: 0%
    _bar("2026-08-07", 105.0),  # Friday: -4.5%
    _bar("2026-08-10", 105.0),  # Monday: 0%
    _bar("2026-08-11", 115.5),  # Tuesday: +10%
    _bar("2026-08-12", 115.5),  # Wednesday: 0%
]


class FakeAlpacaClients:
    pass


@pytest.mark.asyncio
async def test_compute_seasonality_buckets_by_weekday(monkeypatch):
    async def fake_get_daily_bars_multi(clients, symbols, lookback_days=730):
        return {symbols[0]: _BARS}

    monkeypatch.setattr(seasonality_module, "get_daily_bars_multi", fake_get_daily_bars_multi)

    result = await compute_seasonality(FakeAlpacaClients(), "TEST", lookback_days=30)

    assert result.symbol == "TEST"
    by_label = {b.label: b for b in result.by_weekday}

    tuesday = by_label["Tuesday"]
    assert tuesday.n == 2
    assert tuesday.win_rate_pct == 100.0
    assert tuesday.mean_return_pct == pytest.approx(10.0, abs=1e-6)

    monday = by_label["Monday"]
    assert monday.n == 1  # first bar has no prior close, so only one Monday return exists
    assert monday.mean_return_pct == pytest.approx(0.0, abs=1e-6)

    # Weekdays with genuinely zero observations must report None, not a
    # fabricated 0.0 -- there are no Saturday/Sunday bars at all here, but
    # every weekday in this fixture does have at least one observation, so
    # assert the *shape* (5 buckets, fixed Mon..Fri order) instead.
    assert [b.label for b in result.by_weekday] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
    ]


@pytest.mark.asyncio
async def test_compute_seasonality_empty_when_insufficient_bars(monkeypatch):
    async def fake_get_daily_bars_multi(clients, symbols, lookback_days=730):
        return {symbols[0]: []}

    monkeypatch.setattr(seasonality_module, "get_daily_bars_multi", fake_get_daily_bars_multi)

    result = await compute_seasonality(FakeAlpacaClients(), "THIN", lookback_days=30)

    assert all(b.n == 0 and b.mean_return_pct is None for b in result.by_weekday)
    assert all(b.n == 0 and b.mean_return_pct is None for b in result.by_opex)
