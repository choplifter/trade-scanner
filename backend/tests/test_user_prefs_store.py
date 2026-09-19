"""Per-user interface preferences: stored, overwritten, scoped per user,
and opaque to the backend (the browser owns each value's shape)."""

import asyncio
import json
import sqlite3

from app.prefs.store import UserPrefsStore


def _store(tmp_path) -> UserPrefsStore:
    store = UserPrefsStore(str(tmp_path / "prefs.sqlite3"))
    asyncio.run(store.init_schema())
    return store


def test_a_value_comes_back_as_it_went_in(tmp_path):
    store = _store(tmp_path)
    settings = {"chartTheme": "midnight", "autoScroll": True, "riskChartHeight": 240, "optionsShortTargets": {"condor": {"mode": "delta", "value": 0.2}}}
    asyncio.run(store.put(1, "app:settings", settings))
    asyncio.run(store.put(1, "layout:mode", "grid"))

    assert asyncio.run(store.all(1)) == {"app:settings": settings, "layout:mode": "grid"}


def test_putting_the_same_key_again_replaces_it(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.put(1, "app:settings", {"colorMode": "dark"}))
    asyncio.run(store.put(1, "app:settings", {"colorMode": "light"}))

    assert asyncio.run(store.all(1)) == {"app:settings": {"colorMode": "light"}}


def test_preferences_are_scoped_per_user(tmp_path):
    """The point of the whole exercise: two people in one browser had one
    colour scheme between them before this."""
    store = _store(tmp_path)
    asyncio.run(store.put(1, "app:settings", {"colorMode": "dark"}))
    asyncio.run(store.put(2, "app:settings", {"colorMode": "light"}))

    assert asyncio.run(store.all(1))["app:settings"] == {"colorMode": "dark"}
    assert asyncio.run(store.all(2))["app:settings"] == {"colorMode": "light"}
    assert asyncio.run(store.all(3)) == {}


def test_delete_removes_one_key_only(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.put(1, "app:settings", {"colorMode": "dark"}))
    asyncio.run(store.put(1, "layout:grid", {"panels": []}))
    asyncio.run(store.delete(1, "layout:grid"))

    assert set(asyncio.run(store.all(1))) == {"app:settings"}


def test_a_row_that_is_not_json_is_skipped_rather_than_failing_the_fetch(tmp_path):
    """Written by an older shape or by hand: the browser then falls back to
    its own copy of that one key instead of losing every setting."""
    store = _store(tmp_path)
    asyncio.run(store.put(1, "app:settings", {"colorMode": "dark"}))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO user_prefs (user_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
            (1, "layout:grid", "not json", "2026-09-19T00:00:00+00:00"),
        )

    assert asyncio.run(store.all(1)) == {"app:settings": {"colorMode": "dark"}}


def test_values_are_stored_as_json_text(tmp_path):
    """So a layout can be read and repaired with sqlite alone."""
    store = _store(tmp_path)
    asyncio.run(store.put(7, "layout:grid", {"panels": [1, 2]}))
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute("SELECT value, updated_at FROM user_prefs WHERE user_id = 7").fetchone()
    assert json.loads(row[0]) == {"panels": [1, 2]}
    assert row[1].endswith("+00:00")
