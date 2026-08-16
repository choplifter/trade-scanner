import asyncio
import json

import httpx

from app.fundamentals.float_bulk import _PAGE_SIZE, fetch_all_float


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _rows(symbols: dict[str, float]) -> list[dict]:
    return [
        {"symbol": s, "floatShares": f, "outstandingShares": f * 2} for s, f in symbols.items()
    ]


def test_fetch_all_float_returns_symbol_to_float():
    def handler(request):
        assert "apikey" in request.url.params
        return httpx.Response(200, json=_rows({"AAPL": 14_669_554_809.0, "UMAC": 24_500_000.0}))

    async def run():
        async with _client(handler) as c:
            return await fetch_all_float(c, "key")

    assert asyncio.run(run()) == {"AAPL": 14_669_554_809.0, "UMAC": 24_500_000.0}


def test_fetch_all_float_skips_zero_and_missing_floats():
    # FMP reports 0 for "unknown" -- keeping it would make those symbols look
    # like the lowest-float names on the board.
    def handler(request):
        return httpx.Response(
            200,
            json=[
                {"symbol": "GOOD", "floatShares": 1_000_000.0},
                {"symbol": "ZERO", "floatShares": 0},
                {"symbol": "NULL", "floatShares": None},
                {"symbol": "MISSING"},
                {"symbol": "JUNK", "floatShares": "not-a-number"},
                {"floatShares": 5_000_000.0},
            ],
        )

    async def run():
        async with _client(handler) as c:
            return await fetch_all_float(c, "key")

    assert asyncio.run(run()) == {"GOOD": 1_000_000.0}


def test_fetch_all_float_paginates_until_a_short_page():
    pages_seen = []

    def handler(request):
        page = int(request.url.params["page"])
        pages_seen.append(page)
        if page == 0:
            return httpx.Response(200, json=_rows({f"S{i}": 1000.0 + i for i in range(_PAGE_SIZE)}))
        # Short page -> last page.
        return httpx.Response(200, json=_rows({"LAST": 42.0}))

    async def run():
        async with _client(handler) as c:
            return await fetch_all_float(c, "key")

    result = asyncio.run(run())
    assert pages_seen == [0, 1], "should stop after the first short page"
    assert len(result) == _PAGE_SIZE + 1
    assert result["LAST"] == 42.0


def test_fetch_all_float_stops_on_a_restricted_plan():
    # /stable/profile-bulk answers 402 on the Starter plan; if shares-float-all
    # ever did the same, this must degrade to "no data" rather than raise.
    def handler(request):
        return httpx.Response(402, text="Restricted Endpoint")

    async def run():
        async with _client(handler) as c:
            return await fetch_all_float(c, "key")

    assert asyncio.run(run()) == {}


def test_fetch_all_float_survives_a_transport_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    async def run():
        async with _client(handler) as c:
            return await fetch_all_float(c, "key")

    assert asyncio.run(run()) == {}


def test_fetch_all_float_survives_non_json():
    def handler(request):
        return httpx.Response(200, text="<html>maintenance</html>")

    async def run():
        async with _client(handler) as c:
            return await fetch_all_float(c, "key")

    assert asyncio.run(run()) == {}


def test_fetch_all_float_handles_an_empty_first_page():
    def handler(request):
        return httpx.Response(200, json=[])

    async def run():
        async with _client(handler) as c:
            return await fetch_all_float(c, "key")

    assert asyncio.run(run()) == {}
