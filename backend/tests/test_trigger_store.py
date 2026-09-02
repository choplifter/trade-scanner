import asyncio
from datetime import UTC, date, datetime, timedelta

from app.options.models import CloseLeg, TriggerCreate
from app.options.trigger_store import ACTIVE, CANCELLED, FAILED, FIRED, ORPHANED, TriggerStore


def _body(**overrides) -> TriggerCreate:
    base = dict(
        underlying="spy",
        expiry=date(2026, 9, 18),
        legs=[CloseLeg(symbol="SPY260918P00740000", qty=1), CloseLeg(symbol="SPY260918P00745000", qty=-1)],
        qty=1,
        close_below=740.0,
    )
    base.update(overrides)
    return TriggerCreate(**base)


def _store(tmp_path) -> TriggerStore:
    store = TriggerStore(str(tmp_path / "t.sqlite3"))
    asyncio.run(store.init_schema())
    return store


def test_create_list_and_cancel_are_per_user_and_account(tmp_path):
    store = _store(tmp_path)
    mine = asyncio.run(store.create(1, "paper", _body()))
    asyncio.run(store.create(2, "paper", _body()))
    asyncio.run(store.create(1, "live", _body()))

    assert mine["status"] == ACTIVE and mine["underlying"] == "SPY" and mine["account"] == "paper"
    assert mine["legs"] == [{"symbol": "SPY260918P00740000", "qty": 1}, {"symbol": "SPY260918P00745000", "qty": -1}]
    assert [t["id"] for t in asyncio.run(store.list_for_user(1, "paper"))] == [mine["id"]]
    assert len(asyncio.run(store.list_for_user(1, "live"))) == 1
    assert len(asyncio.run(store.all_active())) == 3

    assert asyncio.run(store.cancel(2, mine["id"])) is False  # not theirs
    assert asyncio.run(store.cancel(1, mine["id"])) is True
    assert asyncio.run(store.cancel(1, mine["id"])) is False  # already cancelled
    assert len(asyncio.run(store.all_active())) == 2
    assert asyncio.run(store.list_for_user(1, "paper"))[0]["status"] == CANCELLED


def test_finished_triggers_drop_out_of_the_user_list_after_a_day(tmp_path):
    store = _store(tmp_path)
    old = datetime.now(UTC) - timedelta(hours=30)
    t = asyncio.run(store.create(1, "paper", _body(), now=old))
    asyncio.run(store.mark_fired(t["id"], 739.5, "ord-1", now=old))
    assert asyncio.run(store.list_for_user(1, "paper")) == []
    assert asyncio.run(store.list_for_user(1, "paper", include_finished_hours=48))[0]["fired_order_id"] == "ord-1"


def test_mark_transitions(tmp_path):
    store = _store(tmp_path)
    a = asyncio.run(store.create(1, "paper", _body()))
    b = asyncio.run(store.create(1, "paper", _body()))
    c = asyncio.run(store.create(1, "paper", _body()))

    asyncio.run(store.mark_fired(a["id"], 739.9, "ord-a"))
    asyncio.run(store.mark_failed(b["id"], "broker down", 1, final=False))
    asyncio.run(store.mark_orphaned(c["id"], "gone"))
    rows = {t["id"]: t for t in asyncio.run(store.list_for_user(1, "paper"))}
    assert rows[a["id"]]["status"] == FIRED and rows[a["id"]]["fired_price"] == 739.9
    assert rows[b["id"]]["status"] == ACTIVE and rows[b["id"]]["attempts"] == 1 and rows[b["id"]]["last_error"] == "broker down"
    assert rows[c["id"]]["status"] == ORPHANED
    assert [t["id"] for t in asyncio.run(store.all_active())] == [b["id"]]

    asyncio.run(store.mark_failed(b["id"], "still down", 3, final=True))
    assert asyncio.run(store.all_active()) == []
    assert {t["status"] for t in asyncio.run(store.list_for_user(1, "paper"))} == {FIRED, FAILED, ORPHANED}
