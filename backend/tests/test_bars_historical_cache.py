"""The TTL cache in front of get_historical_bars.

Every chart load fetches weekly+monthly+hourly anchors; the cache is what
keeps that from being three Alpaca round-trips per click. The cases pin
the contract: a hit within the TTL costs no fetch, expiry and a different
key do, and the dict cannot grow without bound.
"""

import asyncio
import time

import pytest

from app.market_data import bars as bars_mod


class _FakeBarSet:
    def __init__(self, data):
        self.data = data


class _FakeData:
    def __init__(self):
        self.calls = 0

    def get_stock_bars(self, request):
        self.calls += 1
        symbol = request.symbol_or_symbols
        return _FakeBarSet({symbol: [f"bar-{self.calls}"]})


class _FakeClients:
    feed = "iex"

    def __init__(self):
        self.data = _FakeData()


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch):
    monkeypatch.setattr(bars_mod, "_historical_cache", {})


def test_a_second_request_within_the_ttl_is_a_hit():
    clients = _FakeClients()

    first = asyncio.run(bars_mod.get_historical_bars(clients, "AAA", "1Week"))
    second = asyncio.run(bars_mod.get_historical_bars(clients, "AAA", "1Week"))

    assert clients.data.calls == 1
    assert second is first


def test_each_timeframe_and_symbol_is_its_own_entry():
    clients = _FakeClients()

    asyncio.run(bars_mod.get_historical_bars(clients, "AAA", "1Week"))
    asyncio.run(bars_mod.get_historical_bars(clients, "AAA", "1Month"))
    asyncio.run(bars_mod.get_historical_bars(clients, "BBB", "1Week"))

    assert clients.data.calls == 3


def test_an_expired_entry_is_refetched():
    clients = _FakeClients()
    asyncio.run(bars_mod.get_historical_bars(clients, "AAA", "1Hour"))
    key = ("AAA", "1Hour")
    fetched_at, bars = bars_mod._historical_cache[key]
    bars_mod._historical_cache[key] = (
        time.monotonic() - bars_mod._HISTORICAL_TTL_SECONDS - 1,
        bars,
    )

    asyncio.run(bars_mod.get_historical_bars(clients, "AAA", "1Hour"))

    assert clients.data.calls == 2


def test_the_cache_stays_bounded(monkeypatch):
    """An automation clicking through the universe must not grow the dict
    without limit; the oldest entry -- also the closest to expiry -- goes."""
    monkeypatch.setattr(bars_mod, "_HISTORICAL_MAX_ENTRIES", 2)
    clients = _FakeClients()

    asyncio.run(bars_mod.get_historical_bars(clients, "AAA", "1Week"))
    asyncio.run(bars_mod.get_historical_bars(clients, "BBB", "1Week"))
    asyncio.run(bars_mod.get_historical_bars(clients, "CCC", "1Week"))

    assert len(bars_mod._historical_cache) == 2
    assert ("AAA", "1Week") not in bars_mod._historical_cache
    assert ("CCC", "1Week") in bars_mod._historical_cache
