"""The operator's scanner pause: persisted in app_kv, admin-only to flip,
visible to everyone."""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.auth.store import UserStore
from app.core.kv_store import KeyValueStore
from app.routers import auth, scanners


class _Engine:
    paused = False
    session = "regular"


def test_kv_store_round_trips_bools_and_strings(tmp_path):
    kv = KeyValueStore(str(tmp_path / "db.sqlite3"))
    asyncio.run(kv.init_schema())
    assert asyncio.run(kv.get_bool("scanner_paused")) is False
    asyncio.run(kv.set("scanner_paused", True))
    assert asyncio.run(kv.get_bool("scanner_paused")) is True
    asyncio.run(kv.set("scanner_paused", False))
    assert asyncio.run(kv.get_bool("scanner_paused", True)) is False
    asyncio.run(kv.set("note", "hello"))
    assert asyncio.run(kv.get("note")) == "hello"
    assert asyncio.run(kv.get("missing", "dflt")) == "dflt"


def test_pause_endpoint_is_admin_only_and_persists(tmp_path):
    db = str(tmp_path / "db.sqlite3")
    users = UserStore(db)
    asyncio.run(users.init_schema())
    asyncio.run(users.create_user("owner", "ownerpass1", "Owner"))
    asyncio.run(users.create_user("guest", "guestpass1", "Guest"))
    kv = KeyValueStore(db)
    asyncio.run(kv.init_schema())

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")
    app.include_router(auth.router)
    app.include_router(scanners.router)
    app.state.user_store = users
    app.state.kv_store = kv
    app.state.scanner_engine = _Engine()
    client = TestClient(app)

    assert client.post("/api/auth/login", json={"username": "guest", "password": "guestpass1"}).status_code == 200
    assert client.get("/api/scanners/status").json() == {"paused": False, "session": "regular"}
    assert client.post("/api/scanners/pause", json={"paused": True}).status_code == 403
    client.post("/api/auth/logout")

    assert client.post("/api/auth/login", json={"username": "owner", "password": "ownerpass1"}).status_code == 200
    assert client.post("/api/scanners/pause", json={"paused": True}).json()["paused"] is True
    assert app.state.scanner_engine.paused is True
    assert asyncio.run(kv.get_bool("scanner_paused")) is True
    assert client.get("/api/scanners/status").json()["paused"] is True
    assert client.post("/api/scanners/pause", json={"paused": False}).json()["paused"] is False
    assert asyncio.run(kv.get_bool("scanner_paused")) is False
