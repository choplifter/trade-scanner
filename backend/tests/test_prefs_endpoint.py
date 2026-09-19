"""The preferences endpoints: per user, opaque values, and the guards that
keep a browser from writing anything it likes into the table."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.prefs.store import UserPrefsStore


@pytest.fixture
def api(tmp_path):
    from app.auth.dependency import get_current_user
    from app.routers import prefs

    store = UserPrefsStore(str(tmp_path / "prefs.sqlite3"))
    asyncio.run(store.init_schema())
    app = FastAPI()
    app.include_router(prefs.router)
    app.state.user_prefs_store = store
    user = {"id": 1, "username": "t"}
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), user


def test_a_fresh_account_has_nothing_stored(api):
    client, _user = api
    assert client.get("/api/prefs").json() == {"prefs": {}}


def test_a_value_round_trips_as_the_browser_wrote_it(api):
    client, _user = api
    raw = '{"version":1,"chartTheme":"midnight"}'
    assert client.put("/api/prefs/app:settings", json=raw).status_code == 200
    assert client.get("/api/prefs").json() == {"prefs": {"app:settings": raw}}


def test_each_user_reads_only_their_own(api):
    client, user = api
    client.put("/api/prefs/app:settings", json='{"colorMode":"dark"}')
    user["id"] = 2
    assert client.get("/api/prefs").json() == {"prefs": {}}
    client.put("/api/prefs/app:settings", json='{"colorMode":"light"}')
    assert client.get("/api/prefs").json()["prefs"]["app:settings"] == '{"colorMode":"light"}'
    user["id"] = 1
    assert client.get("/api/prefs").json()["prefs"]["app:settings"] == '{"colorMode":"dark"}'


def test_delete_removes_the_key(api):
    client, _user = api
    client.put("/api/prefs/layout:dock", json="{}")
    assert client.delete("/api/prefs/layout:dock").status_code == 200
    assert client.get("/api/prefs").json() == {"prefs": {}}


def test_a_key_that_is_not_a_storage_key_is_refused(api):
    client, _user = api
    assert client.put("/api/prefs/../etc", json="x").status_code in (404, 422)
    assert client.put("/api/prefs/" + "k" * 65, json="x").status_code == 422
    assert client.delete("/api/prefs/" + "k" * 65).status_code == 422


def test_an_oversized_value_is_refused_rather_than_stored(api):
    client, _user = api
    assert client.put("/api/prefs/layout:dock", json="x" * (256 * 1024 + 1)).status_code == 413
    assert client.get("/api/prefs").json() == {"prefs": {}}
