"""Where the VIX comes from: Cboe, which computes it and serves it
without a key, and FMP only when Cboe cannot be reached."""

import asyncio
import json

import httpx

from app.market_data.market_conditions import fetch_vix, fetch_vix_cboe, fetch_vix_fmp

CBOE_BODY = {
    "timestamp": "2026-09-20 18:23:49",
    "data": {
        "symbol": "^VIX",
        "security_type": "index",
        "current_price": 14.81,
        "price_change": -0.63,
        "price_change_percent": -4.2539,
        "open": 15.26,
        "high": 15.63,
        "low": 14.8,
        "close": 14.81,
    },
}
FMP_BODY = [{"symbol": "^VIX", "price": 15.4, "changePercentage": 2.5}]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_cboe_is_read_without_a_key():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=CBOE_BODY)

    reading = asyncio.run(fetch_vix_cboe(_client(handler)))
    assert reading is not None
    assert reading.price == 14.81
    assert round(reading.change_pct, 2) == -4.25
    assert "cboe.com" in seen[0] and "apikey" not in seen[0]


def test_the_key_provider_is_not_touched_while_cboe_answers():
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json=CBOE_BODY)

    reading = asyncio.run(fetch_vix(_client(handler), "secret-key"))
    assert reading is not None and reading.price == 14.81
    assert hosts == ["cdn.cboe.com"]


def test_fmp_carries_it_when_cboe_is_down():
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "cdn.cboe.com":
            return httpx.Response(503, text="nope")
        return httpx.Response(200, json=FMP_BODY)

    reading = asyncio.run(fetch_vix(_client(handler), "secret-key"))
    assert reading is not None and reading.price == 15.4
    assert hosts == ["cdn.cboe.com", "financialmodelingprep.com"]


def test_nonsense_from_cboe_is_not_a_reading():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"data": {}}))

    assert asyncio.run(fetch_vix_cboe(_client(handler))) is None


def test_without_a_key_cboe_is_the_only_chance():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert asyncio.run(fetch_vix(_client(handler), "")) is None


def test_fmp_alone_still_works_as_before():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FMP_BODY)

    reading = asyncio.run(fetch_vix_fmp(_client(handler), "secret-key"))
    assert reading is not None and reading.change_pct == 2.5
