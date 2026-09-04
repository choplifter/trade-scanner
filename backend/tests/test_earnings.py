"""The next-earnings lookup -- app.market_data.earnings. The row handling
is pure and tested directly; the cache runs against a stub transport rather
than FMP, in the duck-typed-fake style the rest of the suite uses."""

import asyncio
from datetime import date

import httpx
import pytest

from app.market_data.earnings import (
    EarningsCalendar,
    EarningsDate,
    next_earnings_from_rows,
)

TODAY = date(2026, 9, 4)


# --- picking the next report ----------------------------------------------


def test_the_next_report_is_the_soonest_one_not_in_the_past():
    rows = [
        {"symbol": "AMD", "date": "2026-07-29", "epsActual": 1.2},
        {"symbol": "AMD", "date": "2026-10-28", "epsActual": None},
        {"symbol": "AMD", "date": "2027-01-27", "epsActual": None},
    ]

    result = next_earnings_from_rows("AMD", rows, TODAY)

    assert result.report_date == date(2026, 10, 28)
    assert result.days_until == 54


def test_a_report_today_still_counts_as_upcoming():
    rows = [{"date": "2026-09-04"}]

    result = next_earnings_from_rows("AMD", rows, TODAY)

    assert result.report_date == TODAY
    assert result.days_until == 0


def test_only_past_reports_means_nothing_upcoming():
    rows = [{"date": "2026-07-29"}, {"date": "2026-04-29"}]

    assert next_earnings_from_rows("AMD", rows, TODAY) is None


def test_unparseable_and_malformed_rows_are_skipped_not_fatal():
    rows = [
        {"date": None},
        {"date": "not-a-date"},
        {},
        "junk",
        {"date": "2026-10-28"},
    ]

    assert next_earnings_from_rows("AMD", rows, TODAY).report_date == date(2026, 10, 28)


def test_an_empty_response_means_nothing_known():
    assert next_earnings_from_rows("AMD", [], TODAY) is None
    assert next_earnings_from_rows("AMD", None, TODAY) is None


def test_a_timestamped_date_is_read_as_its_day():
    rows = [{"date": "2026-10-28 16:30:00"}]

    assert next_earnings_from_rows("AMD", rows, TODAY).report_date == date(2026, 10, 28)


# --- would a contract be held through it ----------------------------------


def test_a_report_before_the_expiry_is_held_through():
    earnings = EarningsDate(symbol="AMD", report_date=date(2026, 10, 28), days_until=54)

    assert earnings.before(date(2026, 11, 20)) is True
    assert earnings.before(date(2026, 10, 16)) is False


def test_a_report_on_the_expiry_counts_as_held_through():
    earnings = EarningsDate(symbol="AMD", report_date=date(2026, 10, 16), days_until=42)

    # The case where the event decides the outcome outright.
    assert earnings.before(date(2026, 10, 16)) is True


# --- the cache ------------------------------------------------------------


class _Stub:
    """Counts requests and answers them from a canned payload."""

    def __init__(self, payload, status=200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[str] = []

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.params.get("symbol"))
            return httpx.Response(self.status, json=self.payload)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _calendar(stub, **kwargs) -> EarningsCalendar:
    return EarningsCalendar("key", client=stub.client(), today=lambda: TODAY, **kwargs)


def test_a_symbol_is_fetched_once_and_then_served_from_the_cache():
    stub = _Stub([{"date": "2026-10-28"}])
    calendar = _calendar(stub)

    async def run():
        first = await calendar.next_earnings("AMD")
        second = await calendar.next_earnings("amd")
        return first, second

    first, second = asyncio.run(run())

    assert first.report_date == date(2026, 10, 28)
    assert second == first
    assert stub.calls == ["AMD"]


def test_no_upcoming_report_is_cached_too():
    stub = _Stub([{"date": "2026-01-28"}])
    calendar = _calendar(stub)

    async def run():
        await calendar.next_earnings("AMD")
        return await calendar.next_earnings("AMD")

    # "FMP knows of nothing" is a real answer; re-asking would spend the
    # quota on it every time.
    assert asyncio.run(run()) is None
    assert stub.calls == ["AMD"]


def test_a_burst_on_one_symbol_costs_one_request():
    stub = _Stub([{"date": "2026-10-28"}])
    calendar = _calendar(stub)

    async def run():
        return await asyncio.gather(*(calendar.next_earnings("AMD") for _ in range(4)))

    results = asyncio.run(run())

    assert stub.calls == ["AMD"]
    assert all(r == results[0] for r in results)


def test_a_failed_call_answers_none_rather_than_raising():
    stub = _Stub({"Error Message": "bad key"}, status=401)
    calendar = _calendar(stub)

    assert asyncio.run(calendar.next_earnings("AMD")) is None


def test_without_an_fmp_key_nothing_is_requested():
    stub = _Stub([{"date": "2026-10-28"}])
    calendar = EarningsCalendar("", client=stub.client(), today=lambda: TODAY)

    assert asyncio.run(calendar.next_earnings("AMD")) is None
    assert stub.calls == []


def test_a_cached_entry_from_yesterday_is_refetched():
    # days_until is relative to the day it was computed, so a cached entry
    # is wrong the moment the date rolls even though the TTL has not run out.
    stub = _Stub([{"date": "2026-10-28"}])
    day = {"value": TODAY}
    calendar = EarningsCalendar("key", client=stub.client(), today=lambda: day["value"])

    async def run():
        first = await calendar.next_earnings("AMD")
        day["value"] = date(2026, 9, 5)
        second = await calendar.next_earnings("AMD")
        return first, second

    first, second = asyncio.run(run())

    assert stub.calls == ["AMD", "AMD"]
    assert first.days_until == 54
    assert second.days_until == 53


@pytest.mark.parametrize("expiry,expected", [(date(2026, 11, 20), True), (date(2026, 9, 18), False)])
def test_the_serialised_shape_carries_what_a_caller_needs(expiry, expected):
    earnings = next_earnings_from_rows("AMD", [{"date": "2026-10-28"}], TODAY)

    assert earnings.to_dict() == {"report_date": "2026-10-28", "days_until": 54}
    assert earnings.before(expiry) is expected
