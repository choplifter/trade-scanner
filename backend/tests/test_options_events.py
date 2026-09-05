"""Events around an option's life -- app.options.events, the macro calendar
and the optimizer's earnings switch. Pure parts first (moves from closes,
the summary, FMP row filtering), then gather_events and the endpoint with
duck-typed fakes for the calendars and the IV store, the way the rest of
the suite fakes its collaborators."""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.market_data.earnings import EarningsCalendar, EarningsDate, report_dates_from_rows
from app.market_data.macro_calendar import MacroCalendar, MacroEvent, macro_events_from_rows, macro_label
from app.options.events import EarningsMove, closes_by_day, earnings_moves, gather_events, summarize_moves
from app.options.iv_history_store import IvRank
from app.trading.errors import OrderRejected

TODAY = date(2026, 9, 4)


# --- moves over past reports -------------------------------------------------


@dataclass
class _Bar:
    timestamp: datetime
    close: float


def _bars(closes: dict[str, float]) -> list[_Bar]:
    # Alpaca stamps a daily bar at 04:00 UTC of its session day.
    return [_Bar(datetime.fromisoformat(d).replace(hour=4, tzinfo=timezone.utc), c) for d, c in closes.items()]


def test_closes_are_keyed_by_their_eastern_session_day():
    closes = closes_by_day(_bars({"2026-07-29": 100.0, "2026-07-30": 108.0}))

    assert closes == {date(2026, 7, 29): 100.0, date(2026, 7, 30): 108.0}


def test_a_move_spans_the_close_before_the_report_to_the_first_close_after():
    closes = {
        date(2026, 7, 28): 100.0,  # close before
        date(2026, 7, 29): 103.0,  # report day (before-the-open report moves this one)
        date(2026, 7, 30): 108.0,  # first close after (after-the-close report moves this one)
        date(2026, 7, 31): 107.0,
    }

    moves = earnings_moves([date(2026, 7, 29)], closes, today=TODAY)

    assert len(moves) == 1
    assert moves[0].close_before == 100.0 and moves[0].close_after == 108.0
    assert moves[0].move_pct == pytest.approx(8.0)


def test_reports_without_both_closes_are_skipped_and_the_newest_come_first():
    closes = {date(2026, 4, 28): 50.0, date(2026, 4, 30): 45.0, date(2026, 7, 28): 100.0, date(2026, 7, 30): 108.0}
    reports = [date(2026, 1, 28), date(2026, 4, 29), date(2026, 7, 29), date(2026, 10, 28)]

    moves = earnings_moves(reports, closes, today=TODAY)

    # January has no bars around it; October is upcoming, not history.
    assert [m.report_date for m in moves] == [date(2026, 7, 29), date(2026, 4, 29)]
    assert moves[1].move_pct == pytest.approx(-10.0)


def test_the_limit_keeps_the_newest_reports():
    closes = {date(2026, m, 1): 100.0 for m in range(1, 9)} | {date(2026, m, 3): 100.0 + m for m in range(1, 9)}
    reports = [date(2026, m, 2) for m in range(1, 9)]

    moves = earnings_moves(reports, closes, today=TODAY, limit=3)

    assert [m.report_date.month for m in moves] == [8, 7, 6]


def test_the_summary_is_about_size_not_direction():
    moves = [
        EarningsMove(date(2026, 7, 29), 100.0, 108.0),
        EarningsMove(date(2026, 4, 29), 100.0, 96.0),
        EarningsMove(date(2026, 1, 28), 100.0, 101.0),
    ]

    s = summarize_moves(moves)

    assert s["samples"] == 3
    assert s["median_abs_pct"] == 4.0
    assert s["mean_abs_pct"] == pytest.approx(13.0 / 3, abs=0.01)
    assert s["max_abs_pct"] == 8.0
    assert summarize_moves([]) == {"samples": 0, "median_abs_pct": None, "mean_abs_pct": None, "max_abs_pct": None}


# --- the earnings calendar keeps every date ----------------------------------


def test_report_dates_cover_past_and_upcoming_sorted_and_deduplicated():
    rows = [{"date": "2026-10-28"}, {"date": "2026-07-29 16:30:00"}, {"date": "2026-07-29"}, {"date": None}, "junk"]

    assert report_dates_from_rows(rows) == [date(2026, 7, 29), date(2026, 10, 28)]


def test_the_calendar_serves_both_questions_from_one_fetch():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("symbol"))
        return httpx.Response(200, json=[{"date": "2026-07-29"}, {"date": "2026-10-28"}])

    calendar = EarningsCalendar("key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), today=lambda: TODAY)

    async def run():
        dates = await calendar.report_dates("amd")
        nxt = await calendar.next_earnings("AMD")
        return dates, nxt

    dates, nxt = asyncio.run(run())

    assert dates == [date(2026, 7, 29), date(2026, 10, 28)]
    assert nxt == EarningsDate(symbol="AMD", report_date=date(2026, 10, 28), days_until=54)
    assert calls == ["AMD"]


# --- the macro calendar ------------------------------------------------------


def test_macro_labels_pick_the_tracked_releases_only():
    assert macro_label("Fed Interest Rate Decision") == "FOMC"
    assert macro_label("Core CPI MoM") == "CPI"
    assert macro_label("CPI (Sep)") == "CPI"
    assert macro_label("Nonfarm Payrolls") == "NFP"
    assert macro_label("Non Farm Payrolls (Sep)") == "NFP"
    assert macro_label("Core PCE Price Index YoY (Aug)") == "PCE"
    assert macro_label("GDP Growth Rate QoQ Adv (Q3)") == "GDP"
    # The look-alikes FMP lists on other days are not the release itself.
    for name in ("Initial Jobless Claims", "Cleveland CPI MoM (Aug)", "FOMC Minutes", "FOMC Economic Projections",
                 "Nonfarm Payrolls Private (Sep)", "U-6 Unemployment Rate (Oct)", "Unemployment Rate (Sep)"):
        assert macro_label(name) is None, name


def test_macro_rows_are_filtered_to_us_tracked_releases_in_the_window_one_per_day_and_label():
    rows = [
        {"date": "2026-09-17 18:00:00", "country": "US", "event": "Fed Interest Rate Decision", "impact": "High"},
        {"date": "2026-09-11 12:30:00", "country": "US", "event": "CPI YoY", "impact": "High"},
        {"date": "2026-09-11 12:30:00", "country": "US", "event": "Core CPI MoM", "impact": "High"},
        {"date": "2026-09-10 12:30:00", "country": "US", "event": "Initial Jobless Claims", "impact": "High"},
        {"date": "2026-09-16 09:00:00", "country": "EU", "event": "CPI YoY", "impact": "High"},
        {"date": "2026-12-16 18:00:00", "country": "US", "event": "Fed Interest Rate Decision", "impact": "High"},
    ]

    events = macro_events_from_rows(rows, TODAY, date(2026, 11, 13))

    assert [(e.date.isoformat(), e.label) for e in events] == [("2026-09-11", "CPI"), ("2026-09-17", "FOMC")]


def test_the_macro_calendar_fetches_once_and_does_not_cache_a_failure():
    status = {"code": 500}
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if status["code"] != 200:
            return httpx.Response(status["code"], json={"Error Message": "x"})
        return httpx.Response(200, json=[{"date": "2026-09-17 18:00:00", "country": "US", "event": "Fed Interest Rate Decision"}])

    calendar = MacroCalendar("key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), today=lambda: TODAY)

    async def run():
        first = await calendar.upcoming()
        status["code"] = 200
        second = await calendar.upcoming()
        third = await calendar.upcoming()
        return first, second, third

    first, second, third = asyncio.run(run())

    assert first == []
    assert second == third == [MacroEvent(date(2026, 9, 17), "FOMC", "Fed Interest Rate Decision")]
    assert calls == 2
    assert MacroCalendar("").configured is False


# --- gather_events -----------------------------------------------------------


class _Calendar:
    configured = True

    def __init__(self, dates):
        self.dates = dates

    async def report_dates(self, symbol):
        return list(self.dates)

    async def next_earnings(self, symbol):
        upcoming = [d for d in self.dates if d >= TODAY]
        return EarningsDate(symbol, upcoming[0], (upcoming[0] - TODAY).days) if upcoming else None


class _Macro:
    configured = True

    async def upcoming(self):
        return [MacroEvent(date(2026, 9, 17), "FOMC", "Fed Interest Rate Decision")]


class _IvStore:
    def __init__(self):
        self.recorded: list = []

    async def rank(self, symbol, current):
        return IvRank(percent=72.0, samples=40, low=0.15, high=0.45), 40

    async def record(self, symbol, session_date, atm_iv, dte):
        self.recorded.append((symbol, session_date, atm_iv, dte))


class _Clients:
    pass


def test_gather_events_reports_every_block_with_its_history(monkeypatch):
    async def fake_bars(clients, symbols, lookback_days=14):
        return {"AMD": _bars({"2026-07-28": 100.0, "2026-07-30": 108.0, "2026-04-28": 50.0, "2026-04-30": 47.0})}

    import app.market_data.bars as bars

    monkeypatch.setattr(bars, "get_daily_bars_multi", fake_bars)
    calendar = _Calendar([date(2026, 4, 29), date(2026, 7, 29), date(2026, 10, 28)])
    store = _IvStore()

    payload = asyncio.run(
        gather_events("amd", earnings_calendar=calendar, macro_calendar=_Macro(), iv_store=store, clients=_Clients(), atm_iv=0.31, dte=21, today=TODAY)
    )

    assert payload["underlying"] == "AMD"
    # Today's reading is recorded for the rank's history when the caller
    # says which expiry it belongs to; without a dte nothing is recorded.
    assert store.recorded == [("AMD", TODAY, 0.31, 21)]
    asyncio.run(gather_events("amd", iv_store=store, atm_iv=0.31, today=TODAY))
    assert len(store.recorded) == 1
    e = payload["earnings"]
    assert e["report_date"] == "2026-10-28" and e["days_until"] == 54
    assert [h["move_pct"] for h in e["history"]] == [8.0, -6.0]
    assert e["median_abs_pct"] == 7.0 and e["samples"] == 2 and e["history_note"] is None
    assert payload["macro"] == [{"date": "2026-09-17", "label": "FOMC", "event": "Fed Interest Rate Decision"}]
    assert payload["iv"]["rank"]["percent"] == 72.0 and payload["iv"]["atm_iv"] == 0.31
    assert payload["sources"] == {"earnings": True, "macro": True, "iv_rank": True}


def test_gather_events_says_what_is_missing_instead_of_guessing():
    payload = asyncio.run(gather_events("AMD", today=TODAY))

    assert payload["earnings"] is None and payload["macro"] == []
    assert payload["iv"] == {"atm_iv": None, "rank": None, "samples": 0}
    assert payload["sources"] == {"earnings": False, "macro": False, "iv_rank": False}

    # Dates but no bars client: the next report is known, the history is not.
    payload = asyncio.run(gather_events("AMD", earnings_calendar=_Calendar([date(2026, 7, 29), date(2026, 10, 28)]), today=TODAY))
    assert payload["earnings"]["report_date"] == "2026-10-28"
    assert payload["earnings"]["history"] == [] and payload["earnings"]["history_note"]


@pytest.fixture
def api():
    from app.auth.dependency import get_current_user
    from app.routers import trading_options

    app = FastAPI()
    app.include_router(trading_options.router, prefix="/api/trading/options")
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "t"}
    app.state.earnings_calendar = _Calendar([date(2026, 10, 28)])
    app.state.macro_calendar = _Macro()
    app.state.iv_history_store = _IvStore()
    return app


def test_the_events_endpoint_needs_no_broker_account(api):
    client = TestClient(api)

    resp = client.get("/api/trading/options/events/amd", params={"atm_iv": 0.31})

    assert resp.status_code == 200
    body = resp.json()
    assert body["earnings"]["report_date"] == "2026-10-28"
    assert body["macro"][0]["label"] == "FOMC"
    assert body["iv"]["rank"]["percent"] == 72.0
    assert client.get("/api/trading/options/events/amd", params={"atm_iv": -1}).status_code == 422


# --- the optimizer's earnings switch -----------------------------------------


def test_the_optimizer_warns_when_held_through_earnings_and_excludes_on_request():
    from tests.test_options_optimize_endpoint import FAR, MID, NEAR, _req, _Service
    from app.options.optimize import optimize_structures

    service = _Service()
    calendar = _Calendar([MID])  # the report falls on the middle expiry

    def run(**over):
        return asyncio.run(optimize_structures(service, "X", _req(**over), today=TODAY, now=datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc), earnings_calendar=calendar))

    # Holding through: every later expiry is still considered, and it says so.
    held = run(horizon_expiry=FAR)
    assert held["earnings"] == {"report_date": MID.isoformat(), "days_until": (MID - TODAY).days, "held_through": True, "avoided": False}
    assert any("held through the report" in w for w in held["warnings"])

    # Avoiding, with a horizon before the report: the report's expiry and
    # the later one are left out of the chains considered.
    avoided = run(horizon_expiry=NEAR, avoid_earnings=True)
    assert avoided["earnings"]["avoided"] is True and avoided["earnings"]["held_through"] is False
    assert avoided["horizon"]["expiries_considered"] == [NEAR.isoformat()]
    assert any("left out" in w for w in avoided["warnings"])

    # Avoiding with a horizon on or after the report is a contradiction, said as one.
    with pytest.raises(OrderRejected):
        run(horizon_expiry=FAR, avoid_earnings=True)

    # No calendar: the switch cannot act, and the response says so rather than pretending.
    none = asyncio.run(optimize_structures(service, "X", _req(horizon_expiry=NEAR, avoid_earnings=True), today=TODAY, now=datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)))
    assert none["earnings"] is None and any("no earnings calendar" in w for w in none["warnings"])
