import asyncio

from app.trading.journal_store import JournalStore


def _store(tmp_path) -> JournalStore:
    store = JournalStore(str(tmp_path / "journal.sqlite3"))
    asyncio.run(store.init_schema())
    return store


def test_upsert_creates_an_entry(tmp_path):
    store = _store(tmp_path)
    entry = asyncio.run(store.upsert_entry(1, "SPY:abc:0", "Good breakout entry", 4, ["disciplined"]))
    assert entry.trade_id == "SPY:abc:0"
    assert entry.note == "Good breakout entry"
    assert entry.rating == 4
    assert entry.tags == ["disciplined"]

    entries = asyncio.run(store.list_entries(1))
    assert set(entries) == {"SPY:abc:0"}
    assert entries["SPY:abc:0"].note == "Good breakout entry"


def test_upsert_updates_an_existing_entry(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.upsert_entry(1, "SPY:abc:0", "First draft", None, []))
    asyncio.run(store.upsert_entry(1, "SPY:abc:0", "Revised note", 2, ["fomo", "revenge trade"]))

    entries = asyncio.run(store.list_entries(1))
    assert len(entries) == 1
    assert entries["SPY:abc:0"].note == "Revised note"
    assert entries["SPY:abc:0"].rating == 2
    assert entries["SPY:abc:0"].tags == ["fomo", "revenge trade"]


def test_entries_are_scoped_per_user(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.upsert_entry(1, "SPY:abc:0", "User 1's note", None, []))
    asyncio.run(store.upsert_entry(2, "SPY:abc:0", "User 2's note", None, []))

    assert asyncio.run(store.list_entries(1))["SPY:abc:0"].note == "User 1's note"
    assert asyncio.run(store.list_entries(2))["SPY:abc:0"].note == "User 2's note"


def test_trade_with_no_entry_is_absent_not_empty(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.upsert_entry(1, "SPY:abc:0", "Noted", None, []))

    entries = asyncio.run(store.list_entries(1))
    assert "QQQ:xyz:0" not in entries
