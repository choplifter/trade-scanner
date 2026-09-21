"""Where the 10-year yield comes from: Cboe's ^TNX, which is the yield
times ten, and FMP's end-of-day treasury rates only when Cboe cannot be
reached."""

import asyncio

import httpx
import pytest

from app.market_data.market_conditions import (
    compute_market_conditions,
    fetch_ten_year,
    fetch_ten_year_cboe,
    fetch_ten_year_fmp,
)

CBOE_BODY = {
    "timestamp": "2026-09-21 08:55:06",
    "data": {
        "symbol": "^TNX",
        "security_type": "index",
        "current_price": 49.98,
        "price_change": 0.35,
        "price_change_percent": 0.705,
        "close": 49.98,
    },
}
FMP_BODY = [
    {"date": "2026-09-18", "year2": 4.76, "year10": 5.01},
    {"date": "2026-09-17", "year2": 4.67, "year10": 4.94},
]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_cboe_tnx_is_scaled_to_percent_and_basis_points():
    reading = asyncio.run(fetch_ten_year_cboe(_client(lambda r: httpx.Response(200, json=CBOE_BODY))))
    assert reading is not None
    assert reading.yield_pct == pytest.approx(4.998)
    assert reading.change_bp == pytest.approx(3.5)


def test_fmp_change_is_against_the_previous_day():
    reading = asyncio.run(fetch_ten_year_fmp(_client(lambda r: httpx.Response(200, json=FMP_BODY)), "k"))
    assert reading is not None
    assert reading.yield_pct == pytest.approx(5.01)
    assert reading.change_bp == pytest.approx(7.0)


def test_fmp_is_not_touched_while_cboe_answers():
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json=CBOE_BODY)

    reading = asyncio.run(fetch_ten_year(_client(handler), "k"))
    assert reading is not None
    assert hosts == ["cdn.cboe.com"]


def test_falls_back_to_fmp_when_cboe_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.cboe.com":
            return httpx.Response(503)
        return httpx.Response(200, json=FMP_BODY)

    reading = asyncio.run(fetch_ten_year(_client(handler), "k"))
    assert reading is not None
    assert reading.yield_pct == pytest.approx(5.01)


def test_no_fallback_without_a_key():
    reading = asyncio.run(fetch_ten_year(_client(lambda r: httpx.Response(503)), ""))
    assert reading is None


def test_yield_is_shown_but_does_not_move_the_level():
    from app.market_data.market_conditions import TenYearReading

    conditions = compute_market_conditions(None, [], None, TenYearReading(yield_pct=6.5, change_bp=40))
    assert conditions.level == "green"
    assert conditions.ten_year is not None
