"""The on-demand GEX cache -- app.market_data.gex_cache. A counting fake
stands in for fetch_gex and a hand-driven clock for time.monotonic, in the
style of test_chain_normalise.py's cache tests: no network, no SDK calls,
and the TTL is exercised rather than waited out."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.market_data.gamma_exposure import GexReading, StrikeGex
from app.market_data.gex_cache import GexCache

TTL = 300.0


def _reading(symbol="SPY", spot=750.0) -> GexReading:
    return GexReading(
        symbol=symbol,
        spot_price=spot,
        net_gex=1_000.0,
        call_gex=1_500.0,
        put_gex=-500.0,
        contracts_used=12,
        as_of=datetime.now(timezone.utc),
        by_strike=[StrikeGex(strike=750.0, net_gex=1_000.0, call_gex=1_500.0, put_gex=-500.0)],
        open_interest_used=4_200,
    )


class _Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _Fetcher:
    """Counts calls per symbol; `fails` names symbols that produce nothing.
    `gate` (when set) holds every call until it is released, which is how
    the single-flight test gets two callers in flight at once."""

    def __init__(self, fails=(), gate: asyncio.Event | None = None) -> None:
        self.calls: list[str] = []
        self.fails = set(fails)
        self.gate = gate

    async def __call__(self, clients, symbol) -> GexReading | None:
        self.calls.append(symbol)
        if self.gate is not None:
            await self.gate.wait()
        return None if symbol in self.fails else _reading(symbol)

    def count(self, symbol: str) -> int:
        return self.calls.count(symbol)


def _cache(fetcher, clock, **kwargs) -> GexCache:
    return GexCache(clients=None, ttl=TTL, fetcher=fetcher, now=clock, **kwargs)


def test_a_cold_symbol_is_fetched_and_a_warm_one_is_not():
    async def run():
        fetcher, clock = _Fetcher(), _Clock()
        cache = _cache(fetcher, clock)

        first = await cache.reading("AMD")
        second = await cache.reading("AMD")

        assert first is second
        assert fetcher.count("AMD") == 1

    asyncio.run(run())


def test_the_symbol_is_normalised_to_upper_case():
    async def run():
        fetcher, clock = _Fetcher(), _Clock()
        cache = _cache(fetcher, clock)

        await cache.reading("amd")
        await cache.reading("AMD")

        assert fetcher.calls == ["AMD"]

    asyncio.run(run())


def test_a_reading_is_refetched_once_the_ttl_has_passed():
    async def run():
        fetcher, clock = _Fetcher(), _Clock()
        cache = _cache(fetcher, clock)

        await cache.reading("AMD")
        clock.advance(TTL - 1)
        await cache.reading("AMD")
        assert fetcher.count("AMD") == 1

        clock.advance(2)
        await cache.reading("AMD")
        assert fetcher.count("AMD") == 2

    asyncio.run(run())


def test_a_burst_on_one_symbol_costs_a_single_fetch():
    async def run():
        gate = asyncio.Event()
        fetcher, clock = _Fetcher(gate=gate), _Clock()
        cache = _cache(fetcher, clock)

        callers = [asyncio.create_task(cache.reading("MSFT")) for _ in range(4)]
        await asyncio.sleep(0)  # let them all reach the lock
        gate.set()
        results = await asyncio.gather(*callers)

        assert fetcher.count("MSFT") == 1
        assert all(r is results[0] for r in results)

    asyncio.run(run())


def test_a_burst_on_different_symbols_fetches_each_once():
    async def run():
        gate = asyncio.Event()
        fetcher, clock = _Fetcher(gate=gate), _Clock()
        cache = _cache(fetcher, clock)

        callers = [asyncio.create_task(cache.reading(s)) for s in ("AMD", "MSFT", "AMD")]
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(*callers)

        assert sorted(fetcher.calls) == ["AMD", "MSFT"]

    asyncio.run(run())


def test_a_symbol_that_produces_nothing_answers_none():
    async def run():
        fetcher, clock = _Fetcher(fails={"NOPE"}), _Clock()
        cache = _cache(fetcher, clock)

        assert await cache.reading("NOPE") is None
        assert cache.cached() == {}

    asyncio.run(run())


def test_a_failing_symbol_is_not_refetched_on_every_request():
    async def run():
        fetcher, clock = _Fetcher(fails={"NOPE"}), _Clock()
        cache = _cache(fetcher, clock, failure_backoff=60.0)

        await cache.reading("NOPE")
        await cache.reading("NOPE")
        await cache.reading("NOPE")
        assert fetcher.count("NOPE") == 1

        clock.advance(61)
        await cache.reading("NOPE")
        assert fetcher.count("NOPE") == 2

    asyncio.run(run())


def test_a_failed_refresh_keeps_the_previous_reading():
    async def run():
        fetcher, clock = _Fetcher(), _Clock()
        cache = _cache(fetcher, clock)

        good = await cache.reading("AMD")
        fetcher.fails.add("AMD")
        clock.advance(TTL + 1)

        assert await cache.reading("AMD") is good
        assert "AMD" in cache.cached()

    asyncio.run(run())


def test_refresh_ignores_the_ttl_and_the_backoff():
    async def run():
        fetcher, clock = _Fetcher(), _Clock()
        cache = _cache(fetcher, clock)

        await cache.reading("SPY")
        await cache.refresh("SPY")
        await cache.refresh("SPY")

        assert fetcher.count("SPY") == 3

    asyncio.run(run())


def test_the_least_recently_used_symbol_is_evicted():
    async def run():
        fetcher, clock = _Fetcher(), _Clock()
        cache = _cache(fetcher, clock, max_symbols=2)

        await cache.reading("A")
        await cache.reading("B")
        await cache.reading("A")  # A is now the more recently used of the two
        await cache.reading("C")

        assert sorted(cache.cached()) == ["A", "C"]

    asyncio.run(run())


def test_peek_answers_from_the_cache_without_fetching():
    async def run():
        fetcher, clock = _Fetcher(), _Clock()
        cache = _cache(fetcher, clock)

        assert cache.peek("AMD") is None
        assert fetcher.calls == []

        await cache.reading("AMD")
        clock.advance(TTL + 1)

        # Stale, but peek is for callers that must not block -- it answers
        # with what is there rather than going and getting a fresh one.
        assert cache.peek("amd") is not None
        assert fetcher.count("AMD") == 1

    asyncio.run(run())


def test_cached_reports_every_held_reading():
    async def run():
        fetcher, clock = _Fetcher(), _Clock()
        cache = _cache(fetcher, clock)

        await cache.reading("SPY")
        await cache.reading("QQQ")

        assert sorted(cache.cached()) == ["QQQ", "SPY"]

    asyncio.run(run())


def test_compute_gex_reports_the_open_interest_behind_the_number():
    from app.market_data.gamma_exposure import compute_gex

    reading = compute_gex(
        "AMD",
        200.0,
        [(0.01, 1_000, True, 200.0), (0.02, 250, False, 195.0)],
        datetime.now(timezone.utc),
    )

    assert reading.contracts_used == 2
    assert reading.open_interest_used == 1_250


@pytest.mark.parametrize("symbol", ["", "   "])
def test_a_blank_symbol_is_still_handled_rather_than_crashing(symbol):
    async def run():
        fetcher, clock = _Fetcher(fails={symbol.upper().strip()}), _Clock()
        cache = _cache(fetcher, clock)
        # Nothing here should raise; the fetcher decides there is no such
        # thing, which is the same path a delisted ticker takes.
        await cache.reading(symbol)

    asyncio.run(run())
