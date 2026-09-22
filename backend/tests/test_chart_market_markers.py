"""The chart's 10-year and macro-release markers: where the data comes
from (app.market_data.cboe_history, MacroHistory) and what the two
indicators hand the chart (app.indicators.ten_year_moves, macro_releases)."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.indicators import macro_releases, ten_year_moves
from app.indicators.context import build_context
from app.market_data.cboe_history import CboeHistory, Point, parse_daily, parse_intraday
from app.market_data.macro_calendar import MacroEvent, MacroHistory

UTC = timezone.utc


# --- Cboe ----------------------------------------------------------------


def test_intraday_times_are_new_york_wall_clock_converted_to_utc():
    body = {"data": [{"datetime": "2026-09-18T09:31:00", "price": {"close": 49.88}}]}
    [point] = parse_intraday(body, "_TNX")
    # 09:31 New York in September (EDT, UTC-4) is 13:31 UTC.
    assert point.at == datetime(2026, 9, 18, 13, 31, tzinfo=UTC)


def test_tnx_is_scaled_to_percent_and_zero_prints_are_dropped():
    body = {
        "data": [
            {"datetime": "2026-09-18T09:30:00", "price": {"close": 0.0}},
            {"datetime": "2026-09-18T09:31:00", "price": {"close": 49.88}},
        ]
    }
    points = parse_intraday(body, "_TNX")
    assert [p.value for p in points] == [pytest.approx(4.988)]


def test_daily_closes_land_on_new_york_midnight_like_alpacas_daily_bars():
    [point] = parse_daily({"data": [{"date": "2026-01-15", "close": "41.50"}]}, "_TNX")
    # EST in January: midnight New York is 05:00 UTC.
    assert point.at == datetime(2026, 1, 15, 5, 0, tzinfo=UTC)
    assert point.value == pytest.approx(4.15)


def test_history_is_cached_and_a_failure_is_not():
    calls = []
    status = {"code": 500}

    def handler(request):
        calls.append(request.url.path)
        if status["code"] != 200:
            return httpx.Response(status["code"])
        return httpx.Response(200, json={"data": [{"date": "2026-09-18", "close": "50.0"}]})

    async def run():
        history = CboeHistory(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "_TNX")
        assert await history.daily() == []
        status["code"] = 200
        first = await history.daily()
        second = await history.daily()
        return first, second

    first, second = asyncio.run(run())
    assert first == second and len(first) == 1
    assert calls == ["/api/global/delayed_quotes/charts/historical/_TNX.json"] * 2


# --- macro history -------------------------------------------------------


def test_macro_history_keeps_the_release_minute_and_caches_past_quarters():
    calls = []

    def handler(request):
        calls.append(request.url.params["from"])
        return httpx.Response(
            200,
            json=[
                {"date": "2026-09-16 18:00:00", "country": "US", "event": "Fed Interest Rate Decision"},
                {"date": "2026-09-11 12:30:00", "country": "US", "event": "CPI s.a (Aug)"},
            ],
        )

    async def run():
        history = MacroHistory("key", httpx.AsyncClient(transport=httpx.MockTransport(handler)), today=lambda: date(2027, 1, 1))
        first = await history.between(date(2026, 9, 10), date(2026, 9, 20))
        await history.between(date(2026, 9, 10), date(2026, 9, 20))
        return first

    events = asyncio.run(run())
    assert [(e.label, e.at) for e in events] == [
        ("CPI", datetime(2026, 9, 11, 12, 30, tzinfo=UTC)),
        ("FOMC", datetime(2026, 9, 16, 18, 0, tzinfo=UTC)),
    ]
    assert len(calls) == 1, "a settled quarter is fetched once"


def test_macro_history_without_a_key_is_empty_not_an_error():
    async def run():
        return await MacroHistory("", httpx.AsyncClient()).between(date(2026, 1, 1), date(2026, 2, 1))

    assert asyncio.run(run()) == []


# --- the indicators ------------------------------------------------------


def _bars(start, step, n):
    return [
        SimpleNamespace(timestamp=start + step * i, open=1.0, high=1.0, low=1.0, close=1.0, volume=1, vwap=1.0)
        for i in range(n)
    ]


def test_releases_become_markers_at_their_minute_joined_when_they_coincide():
    bars = _bars(datetime(2026, 9, 11, 8, 0, tzinfo=UTC), timedelta(minutes=1), 600)
    at = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
    events = [
        MacroEvent(date(2026, 9, 11), "CPI", "CPI", at=at),
        MacroEvent(date(2026, 9, 11), "GDP", "GDP", at=at),
        MacroEvent(date(2026, 9, 11), "FOMC", "FOMC", at=datetime(2026, 9, 11, 18, 0, tzinfo=UTC)),
    ]
    ctx = build_context("SPY", bars, [], [], macro_events=events, as_of=datetime(2026, 9, 11, 15, 0, tzinfo=UTC))
    markers = macro_releases.compute(ctx)["Release"]

    # The 18:00 FOMC has not happened yet as of 15:00 -- never drawn early.
    assert markers == [{"time": int(at.timestamp()), "position": "aboveBar", "shape": "square", "text": "CPI · GDP"}]


def test_releases_before_the_chart_are_left_out():
    bars = _bars(datetime(2026, 9, 14, 8, 0, tzinfo=UTC), timedelta(minutes=1), 60)
    events = [MacroEvent(date(2026, 9, 11), "CPI", "CPI", at=datetime(2026, 9, 11, 12, 30, tzinfo=UTC))]
    assert macro_releases.compute(build_context("SPY", bars, [], [], macro_events=events)) == {}


def test_ten_year_points_are_trimmed_to_the_chart_and_to_as_of():
    bars = _bars(datetime(2026, 9, 1, 4, 0, tzinfo=UTC), timedelta(days=1), 10)
    points = [Point(datetime(2020, 1, 1, tzinfo=UTC), 1.9)] + [
        Point(datetime(2026, 9, 1, 4, tzinfo=UTC) + timedelta(days=i), 4.9 + i / 100) for i in range(12)
    ]
    ctx = build_context(
        "SPY", bars, [], [], "1Day", chart_bars=bars, ten_year=points, as_of=datetime(2026, 9, 8, tzinfo=UTC)
    )
    series = ten_year_moves.compute(ctx)["10Y"]
    assert series[0]["t"] == "2026-09-01T04:00:00+00:00"
    assert series[-1]["t"] == "2026-09-07T04:00:00+00:00"


@pytest.mark.parametrize("timeframe, intraday", [("1Min", True), ("1Hour", True), ("4Hour", True), ("1Day", False), ("1Week", False)])
def test_intraday_charts_get_the_minute_history_and_coarser_ones_the_daily(timeframe, intraday):
    assert ten_year_moves.wants_intraday(timeframe) is intraday


def test_a_quarter_outside_the_plan_is_never_asked_again_and_a_failure_waits():
    calls = []
    clock = {"t": 0.0}

    def handler(request):
        start = request.url.params["from"]
        calls.append(start)
        if start < "2025-06-01":
            return httpx.Response(402, text="Premium Query Parameter")
        return httpx.Response(429, json={"Error Message": "Limit Reach"})

    async def run():
        history = MacroHistory(
            "key",
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            now=lambda: clock["t"],
            today=lambda: date(2027, 1, 1),
        )
        window = (date(2025, 3, 1), date(2025, 9, 1))
        await history.between(*window)
        first_round = len(calls)
        await history.between(*window)
        clock["t"] += 16 * 60
        await history.between(*window)
        return first_round

    first_round = asyncio.run(run())
    refused = [c for c in calls if c < "2025-06-01"]
    limited = [c for c in calls if c >= "2025-06-01"]
    # Each refused quarter asked exactly once; the rate-limited one skipped
    # on the immediate reload, retried after the wait.
    assert len(refused) == len(set(refused))
    assert len(calls) == first_round + len(set(limited))
