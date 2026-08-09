import asyncio

import httpx

from app.core.config import Settings
from app.fundamentals.cache import FundamentalsCache


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "shares-float" in str(request.url):
            return httpx.Response(200, json=[{"symbol": "AAPL", "floatShares": 1.0e9}])
        if "profile" in str(request.url):
            return httpx.Response(
                200, json=[{"symbol": "AAPL", "marketCap": 3.0e12, "companyName": "Apple Inc."}]
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_ensure_fresh_works_across_separate_event_loops():
    """Regression test: FundamentalsCache.ensure_fresh() is called both
    from FastAPI's long-lived event loop (ScannerEngine's poll loop) and,
    for an on-demand single-symbol lookup (see app.symbols.info), from a
    Dash callback via async_bridge.run_async -- which does asyncio.run()
    fresh per call, i.e. a *different* event loop than FastAPI's.

    A Semaphore created once as an instance attribute in
    FundamentalsCache.__init__ raised "bound to a different event loop"
    the first time a second, separate event loop tried to use it. This
    reproduces that exact shape: two independent asyncio.run() calls
    against the same cache instance, standing in for FastAPI's loop and
    Dash's run_async loop respectively.
    """
    settings = Settings(fmp_api_key="x", fundamentals_refresh_interval=21600.0)
    client = httpx.AsyncClient(transport=_mock_transport())
    cache = FundamentalsCache(settings, client)

    # First "loop" -- stands in for FastAPI's long-lived loop / ScannerEngine.
    asyncio.run(cache.ensure_fresh(["AAPL"]))
    data = cache.get("AAPL")
    assert data is not None
    assert data.market_cap == 3.0e12

    # Force it stale so the second call actually re-fetches instead of
    # short-circuiting on the TTL check.
    cache._fetched_at["AAPL"] = -float("inf")

    # Second, separate event loop -- stands in for a Dash callback's
    # run_async. This is what raised RuntimeError before the fix.
    asyncio.run(cache.ensure_fresh(["AAPL"]))
    data = cache.get("AAPL")
    assert data is not None
    assert data.market_cap == 3.0e12
    assert data.profile is not None
    assert data.profile.name == "Apple Inc."

    asyncio.run(client.aclose())
