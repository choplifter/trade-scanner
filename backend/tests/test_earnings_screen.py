"""The Earnings screen's data: the calendar by date, the liquidity cut,
the per-symbol facts, and the two read-only endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.market_data.earnings_screen import (
    EarningsDayCalendar,
    Reporter,
    build_earnings_screen,
    clear_facts_cache,
    gather_symbol_facts,
    implied_move_pct,
    liquidity_from_bars,
    reporters_from_rows,
)
from app.services.market_clock import next_trading_day
from tests.test_options_optimize_endpoint import NEAR, TODAY, _Service

WINDOW = (date(2026, 9, 9), date(2026, 9, 10))


# --- the calendar rows ---------------------------------------------------------


def test_reporters_come_from_the_window_deduped_with_reported_read_off_eps_actual():
    rows = [
        {"symbol": "AEO", "date": "2026-09-09", "epsActual": None, "epsEstimated": 0.22},
        {"symbol": "CHWY", "date": "2026-09-09", "epsActual": 0.36, "epsEstimated": 0.18},
        {"symbol": "aeo", "date": "2026-09-09", "epsActual": None, "epsEstimated": 0.22},  # duplicate
        {"symbol": "ORCL", "date": "2026-09-10", "epsActual": None, "epsEstimated": 1.5},
        {"symbol": "LATE", "date": "2026-09-11", "epsActual": None, "epsEstimated": None},  # outside
        {"symbol": "", "date": "2026-09-09"},
        {"symbol": "BAD", "date": "not a date"},
        "junk",
    ]
    reporters = reporters_from_rows(rows, *WINDOW)

    assert [(r.symbol, r.report_date.isoformat(), r.reported) for r in reporters] == [
        ("AEO", "2026-09-09", False),
        ("CHWY", "2026-09-09", True),
        ("ORCL", "2026-09-10", False),
    ]
    assert reporters[0].eps_estimated == 0.22


class _Stub:
    def __init__(self, payload, status=200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[tuple[str, str]] = []

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append((request.url.params.get("from"), request.url.params.get("to")))
            return httpx.Response(self.status, json=self.payload)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_day_calendar_asks_once_per_window_and_does_not_cache_a_failure():
    clock = {"t": 0.0}
    good = _Stub([{"symbol": "AEO", "date": "2026-09-09", "epsActual": None}])
    calendar = EarningsDayCalendar("key", client=good.client(), now=lambda: clock["t"], today=lambda: WINDOW[0])

    first = asyncio.run(calendar.reporters(*WINDOW))
    second = asyncio.run(calendar.reporters(*WINDOW))
    assert [r.symbol for r in first] == ["AEO"] and second == first
    assert good.calls == [("2026-09-09", "2026-09-10")]

    failing = EarningsDayCalendar("key", client=_Stub({}, status=500).client(), today=lambda: WINDOW[0])
    assert asyncio.run(failing.reporters(*WINDOW)) == []
    assert asyncio.run(failing.reporters(*WINDOW)) == []  # retried, still nothing -- not cached as "nobody"

    assert asyncio.run(EarningsDayCalendar("", client=good.client()).reporters(*WINDOW)) == []
    assert not EarningsDayCalendar("").configured


# --- the next session ----------------------------------------------------------------


def test_next_trading_day_skips_weekends_and_holidays():
    assert next_trading_day(date(2026, 9, 11)) == date(2026, 9, 14)  # Friday -> Monday
    assert next_trading_day(date(2026, 9, 4)) == date(2026, 9, 8)  # Labor Day weekend
    assert next_trading_day(date(2026, 9, 9)) == date(2026, 9, 10)


# --- liquidity and the screen ---------------------------------------------------------


@dataclass
class _Bar:
    timestamp: datetime
    close: float
    volume: float


def _bars(days: dict[str, tuple[float, float]]) -> list[_Bar]:
    return [
        _Bar(datetime.fromisoformat(d).replace(hour=4, tzinfo=timezone.utc), c, v) for d, (c, v) in days.items()
    ]


def test_liquidity_is_the_last_completed_close_and_the_mean_dollar_volume():
    bars = {
        "AEO": _bars({"2026-09-07": (17.0, 1_000_000), "2026-09-08": (17.2, 2_000_000), "2026-09-09": (99.0, 9)}),
        "EMPTY": [],
    }
    liquidity = liquidity_from_bars(bars, today=date(2026, 9, 9))

    assert liquidity["AEO"][0] == 17.2
    assert liquidity["AEO"][1] == pytest.approx((17.0 * 1_000_000 + 17.2 * 2_000_000) / 2)
    assert "EMPTY" not in liquidity


@dataclass
class _Row:
    symbol: str
    company_name: str | None = None
    last_price: float = 0.0
    pct_change: float = 0.0
    dollar_volume_today: float = 0.0
    market_cap: float | None = None
    sector: str | None = None


def test_the_screen_keeps_tradable_liquid_reporters_most_liquid_first():
    reporters = [
        Reporter("AEO", date(2026, 9, 9), False, 0.22),
        Reporter("ORCL", date(2026, 9, 10), False, 1.5),
        Reporter("TINY", date(2026, 9, 9), False, None),  # below the floor
        Reporter("XXXXF", date(2026, 9, 9), False, None),  # not tradable
        Reporter("NOBARS", date(2026, 9, 9), False, None),  # no liquidity known
        Reporter("LATER", date(2026, 9, 14), False, None),  # outside the two sessions
    ]
    tradable = {"AEO": "American Eagle", "ORCL": "Oracle", "TINY": None, "NOBARS": None, "LATER": None}
    liquidity = {"AEO": (17.2, 180e6), "ORCL": (162.0, 6_000e6), "TINY": (3.0, 2e6), "LATER": (10.0, 500e6)}
    live = {"AEO": _Row("AEO", "American Eagle Outfitters", 17.4, 1.2, 50e6, 3.4e9, "Retail")}

    rows = build_earnings_screen(
        reporters, tradable, liquidity, live, today=date(2026, 9, 9), next_session=date(2026, 9, 10), min_dollar_volume=20e6
    )

    assert [(r.symbol, r.session) for r in rows] == [("ORCL", "next"), ("AEO", "today")]
    aeo = rows[1].to_dict()
    assert aeo["company_name"] == "American Eagle Outfitters" and aeo["last"] == 17.4 and aeo["sector"] == "Retail"
    orcl = rows[0].to_dict()
    # No live row: the last completed close stands in and today's numbers are absent, not zero.
    assert orcl["last"] == 162.0 and orcl["pct_change"] is None and orcl["company_name"] == "Oracle"


# --- the facts --------------------------------------------------------------------------


class _Calendar:
    configured = True

    def __init__(self, dates):
        self.dates = dates

    async def report_dates(self, symbol):
        return list(self.dates)


def _facts(service, calendar, bars, *, ttl=0.0):
    import app.market_data.bars as bars_module

    async def fake_bars(clients, symbols, lookback_days=14):
        return bars

    original = bars_module.get_daily_bars_multi
    bars_module.get_daily_bars_multi = fake_bars
    try:
        return asyncio.run(
            gather_symbol_facts(["X"], service=service, clients=object(), earnings_calendar=calendar, today=TODAY, ttl=ttl)
        )
    finally:
        bars_module.get_daily_bars_multi = original


def test_facts_carry_the_front_expiry_implied_move_and_the_past_moves():
    clear_facts_cache()
    calendar = _Calendar([date(2026, 6, 3), date(2026, 9, 9)])
    bars = {"X": _bars({"2026-06-02": (100.0, 1e6), "2026-06-04": (108.0, 1e6)})}
    facts = _facts(_Service(), calendar, bars)["X"]

    assert facts.front_expiry == NEAR and facts.front_dte == (NEAR - TODAY).days and facts.contract_count == 40
    assert facts.atm_iv == pytest.approx(0.30)
    assert facts.implied_move_pct == pytest.approx(implied_move_pct(0.30, (NEAR - TODAY).days))
    assert facts.samples == 1 and facts.hist_median_pct == 8.0 and facts.history_note is None


def test_facts_survive_a_chain_that_cannot_be_read_and_say_what_is_missing():
    clear_facts_cache()

    class _Broken(_Service):
        async def chain(self, underlying, expiry):
            raise RuntimeError("no chain")

    facts = _facts(_Broken(), _Calendar([]), {})["X"]
    assert facts.front_expiry == NEAR and facts.atm_iv is None and facts.implied_move_pct is None
    assert facts.samples == 0 and facts.history_note == "no past report dates known"


def test_facts_are_cached_per_symbol_and_day():
    clear_facts_cache()
    service = _Service()
    _facts(service, _Calendar([]), {}, ttl=300)
    _facts(service, _Calendar([]), {}, ttl=300)
    assert service.chain_calls == [NEAR]


# --- the endpoints -------------------------------------------------------------------


@dataclass(frozen=True)
class _Listing:
    symbol: str
    name: str | None
    exchange: str = "NYSE"


class _Settings:
    has_credentials = True
    scanner_min_dollar_volume = 20e6


class _Engine:
    def __init__(self, rows):
        self.rows = rows
        self.market_conditions = None
        self.gex_cache = None


@pytest.fixture
def api(monkeypatch):
    from app.auth.dependency import get_current_user
    from app.routers import earnings

    import app.market_data.bars as bars_module

    async def fake_bars(clients, symbols, lookback_days=14):
        return {s: _bars({"2026-09-07": (20.0, 5e6), "2026-09-08": (20.0, 5e6)}) for s in symbols}

    monkeypatch.setattr(bars_module, "get_daily_bars_multi", fake_bars)

    app = FastAPI()
    app.include_router(earnings.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "t"}
    service = _Service()
    app.dependency_overrides[earnings._market_service] = lambda: service
    app.state.settings = _Settings()
    app.state.alpaca_clients = object()
    app.state.all_symbols = [_Listing("AEO", "American Eagle"), _Listing("ORCL", "Oracle")]
    app.state.universe = {}
    app.state.scanner_engine = _Engine({})
    app.state.earnings_calendar = _Calendar([])
    good = _Stub(
        [
            {"symbol": "AEO", "date": "2026-09-09", "epsActual": None},
            {"symbol": "ORCL", "date": "2026-09-10", "epsActual": None},
            {"symbol": "XXXXF", "date": "2026-09-09", "epsActual": None},
        ]
    )
    app.state.earnings_day_calendar = EarningsDayCalendar("key", client=good.client(), today=lambda: date(2026, 9, 9))
    return app, service


def test_screen_endpoint_lists_the_two_sessions(api):
    app, _ = api
    body = TestClient(app).get("/api/earnings/screen", params={"date": "2026-09-09"}).json()

    assert body["today"] == "2026-09-09" and body["next_session"] == "2026-09-10"
    assert [(r["symbol"], r["session"]) for r in body["rows"]] == [("AEO", "today"), ("ORCL", "next")]
    assert body["sources"] == {"calendar": True, "bars": True}
    assert body["min_dollar_volume"] == 20e6


def test_screen_endpoint_rejects_a_bad_date_and_survives_no_calendar(api):
    app, _ = api
    client = TestClient(app)
    assert client.get("/api/earnings/screen", params={"date": "yesterday"}).status_code == 422

    app.state.earnings_day_calendar = EarningsDayCalendar("")
    body = client.get("/api/earnings/screen", params={"date": "2026-09-09"}).json()
    assert body["rows"] == [] and body["sources"]["calendar"] is False


def test_facts_endpoint_caps_the_list_and_answers_per_symbol(api):
    clear_facts_cache()
    app, _ = api
    client = TestClient(app)
    body = client.get("/api/earnings/facts", params={"symbols": "aeo, orcl"}).json()
    assert set(body["facts"]) == {"AEO", "ORCL"}
    assert body["facts"]["AEO"]["implied_move_pct"] is not None

    too_many = ",".join(f"S{i}" for i in range(31))
    assert client.get("/api/earnings/facts", params={"symbols": too_many}).status_code == 422
