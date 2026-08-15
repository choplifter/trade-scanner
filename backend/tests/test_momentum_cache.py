from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.scanners import momentum_cache as momentum_cache_module
from app.scanners.momentum_cache import MomentumCache


@dataclass
class FakeBar:
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime


class FakeAlpacaClients:
    settings = Settings()


# Monday 2026-08-03, 14:30 UTC = 10:30 ET -- a real NYSE trading day,
# mid-regular-session. Momentum only reads regular-session bars now (see
# momentum.is_regular_session_bar), so a weekend/holiday base date would
# make every fixture bar ineligible.
_BASE = datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc)


def _flat_bars(n: int, price: float, start: datetime = _BASE) -> list[FakeBar]:
    return [
        FakeBar(
            open=price, high=price, low=price, close=price, volume=1000.0, timestamp=start + timedelta(minutes=5 * i)
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_ensure_fresh_populates_all_fields(monkeypatch):
    # 20 flat 5-minute bars at 100, then the last one jumps to a shaved-top,
    # green close well above the (still ~100) running VWAP -- exercises the
    # full ensure_fresh() wiring end to end (this is what caught a real
    # NameError typo that 79 other passing tests never touched, since
    # nothing else calls MomentumCache.ensure_fresh directly). The bar
    # itself is the confirmation candle at this resolution -- no
    # aggregation step to fake data for.
    bars = _flat_bars(20, 100.0)
    bars[-1] = FakeBar(open=100.0, high=110.0, low=99.0, close=110.0, volume=1000.0, timestamp=bars[-1].timestamp)

    async def fake_get_intraday_5m_bars_multi(clients, symbols):
        return {"AAA": bars}

    monkeypatch.setattr(momentum_cache_module, "get_intraday_5m_bars_multi", fake_get_intraday_5m_bars_multi)

    cache = MomentumCache(Settings(), FakeAlpacaClients())
    await cache.ensure_fresh(["AAA"])

    assert cache.get("AAA") is not None
    assert cache.is_shaved_top("AAA") is True
    assert cache.is_green("AAA") is True
    assert cache.is_above_vwap("AAA") is True


@pytest.mark.asyncio
async def test_ensure_fresh_clears_fields_when_no_bars(monkeypatch):
    async def fake_get_intraday_5m_bars_multi(clients, symbols):
        return {}

    monkeypatch.setattr(momentum_cache_module, "get_intraday_5m_bars_multi", fake_get_intraday_5m_bars_multi)

    cache = MomentumCache(Settings(), FakeAlpacaClients())
    await cache.ensure_fresh(["AAA"])

    assert cache.get("AAA") is None
    assert cache.is_shaved_top("AAA") is False
    assert cache.is_green("AAA") is False
    assert cache.is_above_vwap("AAA") is False


@pytest.mark.asyncio
async def test_ensure_fresh_does_not_refetch_within_interval(monkeypatch):
    call_count = 0

    async def fake_get_intraday_5m_bars_multi(clients, symbols):
        nonlocal call_count
        call_count += 1
        return {"AAA": _flat_bars(20, 100.0)}

    monkeypatch.setattr(momentum_cache_module, "get_intraday_5m_bars_multi", fake_get_intraday_5m_bars_multi)

    cache = MomentumCache(Settings(), FakeAlpacaClients())
    await cache.ensure_fresh(["AAA"])
    await cache.ensure_fresh(["AAA"])

    assert call_count == 1


@pytest.mark.asyncio
async def test_ensure_fresh_clears_fields_outside_the_regular_session(monkeypatch):
    # Bars running past the 16:00 ET close: the last one is an after-hours
    # print. Momentum is regular-session-only (see
    # momentum.is_regular_session_bar), so everything clears rather than
    # reporting a reading measured across the close -- which also stops the
    # alarm firing on it. Mirrors the backtest's own after-hours guard.
    after_hours_run = datetime(2026, 8, 3, 19, 40, tzinfo=timezone.utc)  # 15:40 ET
    bars = [
        FakeBar(open=100.0, high=100.0, low=100.0, close=100.0, volume=50_000.0, timestamp=after_hours_run),
        FakeBar(
            open=100.0, high=100.0, low=100.0, close=100.0, volume=50_000.0,
            timestamp=after_hours_run + timedelta(minutes=5),
        ),
        FakeBar(
            open=100.0, high=100.0, low=100.0, close=100.0, volume=50_000.0,
            timestamp=after_hours_run + timedelta(minutes=10),
        ),
        # 16:10 ET -- after the close, big jump, shaved top, green, thin.
        FakeBar(
            open=109.0, high=110.0, low=109.0, close=110.0, volume=300.0,
            timestamp=after_hours_run + timedelta(minutes=30),
        ),
    ]

    async def fake_get_intraday_5m_bars_multi(clients, symbols):
        return {"AAA": bars}

    monkeypatch.setattr(momentum_cache_module, "get_intraday_5m_bars_multi", fake_get_intraday_5m_bars_multi)

    cache = MomentumCache(Settings(), FakeAlpacaClients())
    await cache.ensure_fresh(["AAA"])

    assert cache.get("AAA") is None
    assert cache.is_shaved_top("AAA") is False
    assert cache.is_green("AAA") is False
    assert cache.is_above_vwap("AAA") is False
