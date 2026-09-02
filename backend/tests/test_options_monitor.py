"""check_one with a fake service and store: what fires, what is left alone,
what gets parked."""

import asyncio
from dataclasses import dataclass, field

from app.core.config import Settings
from app.options.monitor import MAX_ATTEMPTS, check_one, is_hit


def test_is_hit_bounds_are_inclusive():
    t = {"close_below": 740.0, "close_above": 760.0}
    assert is_hit(t, 740.0) == "below"
    assert is_hit(t, 739.0) == "below"
    assert is_hit(t, 760.0) == "above"
    assert is_hit(t, 750.0) is None
    assert is_hit({"close_below": None, "close_above": 760.0}, 700.0) is None
    assert is_hit({"close_below": 740.0, "close_above": None}, 800.0) is None


@dataclass
class _Leg:
    symbol: str
    qty: int


@dataclass
class _Group:
    legs: list


@dataclass
class _FakeService:
    held: dict
    closes: list = field(default_factory=list)
    fail: bool = False

    async def spreads(self):
        return [_Group([_Leg(symbol, qty) for symbol, qty in self.held.items()])]

    async def close_spread(self, req, confirm=None, *, marketable=False):
        if self.fail:
            raise RuntimeError("broker down")
        self.closes.append((req, confirm, marketable))
        return {"id": "ord-1"}


@dataclass
class _FakeStore:
    fired: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    orphaned: list = field(default_factory=list)

    async def mark_fired(self, trigger_id, price, order_id):
        self.fired.append((trigger_id, price, order_id))

    async def mark_failed(self, trigger_id, error, attempts, *, final):
        self.failed.append((trigger_id, error, attempts, final))

    async def mark_orphaned(self, trigger_id, reason):
        self.orphaned.append((trigger_id, reason))


def _settings(**overrides) -> Settings:
    kwargs = {"alpaca_api_key_id": "k", "alpaca_api_secret_key": "s", "trading_enabled": True}
    kwargs.update(overrides)
    return Settings(**kwargs)


def _trigger(**overrides) -> dict:
    base = {
        "id": "t1",
        "account": "paper",
        "underlying": "SPY",
        "legs": [{"symbol": "SPY260918P00740000", "qty": 1}, {"symbol": "SPY260918P00745000", "qty": -1}],
        "qty": 1,
        "close_below": 740.0,
        "close_above": None,
        "attempts": 0,
    }
    base.update(overrides)
    return base


HELD = {"SPY260918P00740000": 2, "SPY260918P00745000": -2}


def test_not_hit_or_no_price_does_nothing():
    service, store = _FakeService(HELD), _FakeStore()
    asyncio.run(check_one(service, store, _settings(), _trigger(), 745.0))
    asyncio.run(check_one(service, store, _settings(), _trigger(), None))
    assert service.closes == [] and store.fired == [] and store.failed == []


def test_hit_closes_marketably_with_the_held_quantities_and_marks_fired():
    service, store = _FakeService(HELD), _FakeStore()
    asyncio.run(check_one(service, store, _settings(), _trigger(qty=5), 739.5))
    (req, confirm, marketable), = service.closes
    assert marketable is True and confirm == "LIVE"
    assert req.qty == 2  # capped at what is held
    assert [(leg.symbol, leg.qty) for leg in req.legs] == [("SPY260918P00740000", 2), ("SPY260918P00745000", -2)]
    assert store.fired == [("t1", 739.5, "ord-1")]


def test_missing_legs_orphan_the_trigger():
    service, store = _FakeService({"SPY260918P00740000": 1}), _FakeStore()
    asyncio.run(check_one(service, store, _settings(), _trigger(), 739.5))
    assert service.closes == [] and len(store.orphaned) == 1 and store.fired == []


def test_failures_count_attempts_and_park_after_the_limit():
    service, store = _FakeService(HELD, fail=True), _FakeStore()
    asyncio.run(check_one(service, store, _settings(), _trigger(attempts=0), 739.5))
    assert store.failed == [("t1", "broker down", 1, False)]
    asyncio.run(check_one(service, store, _settings(), _trigger(attempts=MAX_ATTEMPTS - 1), 739.5))
    assert store.failed[-1] == ("t1", "broker down", MAX_ATTEMPTS, True)


def test_refused_by_the_gate_is_final_without_touching_the_service():
    service, store = _FakeService(HELD), _FakeStore()
    asyncio.run(check_one(service, store, _settings(trading_enabled=False), _trigger(), 739.5))
    assert service.closes == [] and store.failed[0][3] is True
    # A live trigger with the switch off since arming: same, final.
    asyncio.run(
        check_one(
            service,
            store,
            _settings(alpaca_live_api_key_id="lk", alpaca_live_api_secret_key="ls"),
            _trigger(account="live"),
            739.5,
        )
    )
    assert store.failed[-1][3] is True and service.closes == []
