"""check_one with a fake service and store: what fires, what is left alone,
what gets parked."""

import asyncio
from dataclasses import dataclass, field

from app.core.config import Settings
from app.options.monitor import MAX_ATTEMPTS, check_one, is_hit, premium_of, wants_premium


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

    async def mark_fired(self, trigger_id, price, order_id, *, on="underlying"):
        self.fired.append((trigger_id, price, order_id))
        self.fired_on = on

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


# --- premium bounds ------------------------------------------------------------


@dataclass
class _Quote:
    bid: float
    ask: float
    mid: float


def test_is_hit_on_the_premium_and_a_missing_price_only_silences_its_own_bounds():
    t = {"close_below": 740.0, "close_above": None, "premium_below": 1.0, "premium_above": 3.0}
    assert is_hit(t, 750.0, 1.0) == "premium_below"
    assert is_hit(t, 750.0, 3.5) == "premium_above"
    assert is_hit(t, 750.0, 2.0) is None
    assert is_hit(t, None, 0.5) == "premium_below"
    assert is_hit(t, 739.0, None) == "below"
    # The underlying is checked first when both cross.
    assert is_hit(t, 739.0, 0.5) == "below"
    assert not wants_premium({"premium_below": None, "premium_above": None})
    assert wants_premium({"premium_below": 1.0})


def test_premium_of_is_the_positive_mid_of_the_closing_package():
    long_call = {"legs": [{"symbol": "SPY260918C00750000", "qty": 2}]}
    quotes = {"SPY260918C00750000": _Quote(2.0, 2.2, 2.1)}
    assert premium_of(long_call, quotes) == 2.1
    # A credit spread held short: closing costs a debit, the mark is that cost.
    spread = {"legs": [{"symbol": "SPY260918P00740000", "qty": 1}, {"symbol": "SPY260918P00745000", "qty": -1}]}
    quotes = {"SPY260918P00740000": _Quote(1.0, 1.2, 1.1), "SPY260918P00745000": _Quote(2.0, 2.2, 2.1)}
    assert premium_of(spread, quotes) == 1.0
    # A missing leg quote means no price.
    assert premium_of(spread, {"SPY260918P00740000": _Quote(1.0, 1.2, 1.1)}) is None
    assert premium_of({"legs": []}, quotes) is None


def test_premium_hit_closes_and_records_the_premium():
    service, store = _FakeService(HELD), _FakeStore()
    trigger = _trigger(close_below=None, premium_below=1.5)
    asyncio.run(check_one(service, store, _settings(), trigger, 750.0, 1.4))
    (req, _confirm, marketable), = service.closes
    assert marketable is True and req.qty == 1
    assert store.fired == [("t1", 1.4, "ord-1")] and store.fired_on == "premium"
    # No premium this tick: the premium-only trigger stays armed.
    service, store = _FakeService(HELD), _FakeStore()
    asyncio.run(check_one(service, store, _settings(), trigger, 750.0, None))
    assert service.closes == [] and store.fired == []
