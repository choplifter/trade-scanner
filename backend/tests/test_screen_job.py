"""The background screen: when a pass is due, what it stores, and what the
stored run says about its own age."""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.options.screen_job import LIMIT, STRATEGIES, due, run_once
from app.options.screen_store import ScreenStore
from app.services.market_clock import ET


def _et(hour: int, minute: int = 0, day: int = 1) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=ET)


# --- when --------------------------------------------------------------------


def test_the_first_pass_of_the_session_is_due_and_the_next_waits():
    assert due(_et(10, 0), None)
    assert not due(_et(10, 5), _et(10, 0)), "five minutes is not half an hour"
    assert due(_et(10, 31), _et(10, 0))


def test_nothing_runs_outside_the_regular_session():
    assert not due(_et(7, 0), None), "premarket quotes would replace a good table with a thin one"
    assert not due(_et(18, 0), None)
    assert not due(_et(12, 0, day=3), None), "a Saturday"


# --- what --------------------------------------------------------------------


class _Store:
    def __init__(self):
        self.saved: dict[str, dict] = {}

    async def save(self, strategy, body):
        self.saved[strategy] = body


class _State:
    universe = {"A": object()}
    earnings_calendar = None
    iv_history_store = None


def _run_once(screen_impl, store, factory=lambda: object()):
    import app.options.screen_job as job

    original = job.screen_underlyings
    job.screen_underlyings = screen_impl
    try:
        return asyncio.run(run_once(factory, object(), store, _State()))
    finally:
        job.screen_underlyings = original


def test_a_pass_screens_every_strategy_and_stores_each_answer():
    seen: list[tuple[str, bool, int]] = []

    async def screen(service, clients, req, **kwargs):
        seen.append((req.strategy, req.scan_universe, req.limit))
        return {"rows": [{"symbol": "A"}, {"symbol": "B"}], "strategy": req.strategy}

    store = _Store()
    counts = _run_once(screen, store)
    assert [s[0] for s in seen] == list(STRATEGIES)
    assert all(scan and limit == LIMIT for _strategy, scan, limit in seen), "the universe, at the job's own limit"
    assert counts == {s: 2 for s in STRATEGIES}
    assert set(store.saved) == set(STRATEGIES)


def test_an_empty_answer_does_not_replace_a_good_stored_table():
    async def screen(service, clients, req, **kwargs):
        return {"rows": [], "strategy": req.strategy}

    store = _Store()
    counts = _run_once(screen, store)
    assert counts == {s: 0 for s in STRATEGIES}
    assert store.saved == {}, "nothing priced is not an answer worth keeping"


def test_one_strategy_failing_does_not_stop_the_others():
    async def screen(service, clients, req, **kwargs):
        if req.strategy == STRATEGIES[0]:
            raise RuntimeError("chain fetch failed")
        return {"rows": [{"symbol": "A"}], "strategy": req.strategy}

    store = _Store()
    counts = _run_once(screen, store)
    assert STRATEGIES[0] not in counts and counts[STRATEGIES[1]] == 1


def test_no_broker_means_no_pass_rather_than_a_crash():
    async def screen(service, clients, req, **kwargs):
        raise AssertionError("should not be called")

    store = _Store()
    assert _run_once(screen, store, factory=lambda: None) == {}


# --- the store ---------------------------------------------------------------


def test_a_stored_run_comes_back_whole_and_says_when_it_ran(tmp_path):
    store = ScreenStore(str(tmp_path / "screens.sqlite3"))
    asyncio.run(store.init_schema())
    assert asyncio.run(store.latest("credit_spread")) is None

    asyncio.run(store.save("credit_spread", {"rows": [{"symbol": "T"}], "strategy": "credit_spread"}))
    stored = asyncio.run(store.latest("credit_spread"))
    assert stored["rows"] == [{"symbol": "T"}]
    assert stored["stored_at"].startswith("20"), "the age is what makes a stored table honest"

    listed = asyncio.run(store.strategies())
    assert listed == [{"strategy": "credit_spread", "ran_at": stored["stored_at"], "rows_count": 1}]


def test_a_later_run_replaces_the_earlier_one_for_that_strategy(tmp_path):
    store = ScreenStore(str(tmp_path / "screens.sqlite3"))
    asyncio.run(store.init_schema())
    asyncio.run(store.save("credit_spread", {"rows": [{"symbol": "OLD"}]}))
    asyncio.run(store.save("credit_spread", {"rows": [{"symbol": "NEW"}]}))
    asyncio.run(store.save("cash_secured_put", {"rows": [{"symbol": "CSP"}]}))

    assert asyncio.run(store.latest("credit_spread"))["rows"] == [{"symbol": "NEW"}]
    assert {s["strategy"] for s in asyncio.run(store.strategies())} == {"credit_spread", "cash_secured_put"}


def test_a_failed_write_is_swallowed_rather_than_taking_the_pass_down(tmp_path):
    store = ScreenStore(str(tmp_path / "nope" / "screens.sqlite3"))  # directory does not exist
    asyncio.run(store.save("credit_spread", {"rows": []}))  # must not raise
    assert asyncio.run(store.latest("credit_spread")) is None
