"""Settings → Users: the admin router over a real UserStore, with the
guard rails (no self-delete, no self-demotion, last admin stays)."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.auth.store import UserStore
from app.broker.crypto import SecretBox
from app.broker.store import BrokerStore
from app.routers import admin, auth


@pytest.fixture
def client(tmp_path):
    db = str(tmp_path / "db.sqlite3")
    users = UserStore(db)
    asyncio.run(users.init_schema())
    asyncio.run(users.create_user("owner", "ownerpass1", "Owner"))  # first account: admin
    asyncio.run(users.create_user("guest", "guestpass1", "Guest"))
    broker = BrokerStore(db, SecretBox("k"))
    asyncio.run(broker.init_schema())
    asyncio.run(broker.set(2, "paper", "PKGUEST0000ABCD", "secret", verified_account_number="A1"))

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.state.user_store = users
    app.state.broker_store = broker
    return TestClient(app)


def _login(client, username, password):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["user"]


def test_admin_lists_creates_updates_and_deletes(client):
    owner = _login(client, "owner", "ownerpass1")
    assert owner["is_admin"] is True
    users = client.get("/api/admin/users").json()["users"]
    assert [u["username"] for u in users] == ["owner", "guest"]
    guest = next(u for u in users if u["username"] == "guest")
    assert guest["broker"] == {"paper": "ABCD"} and "password_hash" not in guest

    bad = client.post("/api/admin/users", json={"username": "Bad Name", "display_name": "x", "password": "longenough"})
    assert bad.status_code == 422
    dup = client.post("/api/admin/users", json={"username": "guest", "display_name": "x", "password": "longenough"})
    assert dup.status_code == 422
    created = client.post(
        "/api/admin/users", json={"username": "neighbour", "display_name": "Neighbour", "password": "neighbour1"}
    )
    assert created.status_code == 200
    new_id = created.json()["user"]["id"]
    assert created.json()["user"]["is_admin"] is False and created.json()["user"]["broker"] == {}

    # Promote, rename, reset the password.
    res = client.patch(f"/api/admin/users/{new_id}", json={"is_admin": True, "display_name": "Neighbour N."})
    assert res.status_code == 200 and res.json()["user"]["is_admin"] is True
    assert res.json()["user"]["display_name"] == "Neighbour N."
    assert client.patch(f"/api/admin/users/{new_id}", json={"password": "changed123"}).status_code == 200
    # Guard rails.
    assert client.patch(f"/api/admin/users/{owner['id']}", json={"is_admin": False}).status_code == 422
    assert client.delete(f"/api/admin/users/{owner['id']}").status_code == 422
    assert client.delete("/api/admin/users/999").status_code == 404
    # Demote the new admin again, then the owner is the last admin.
    assert client.patch(f"/api/admin/users/{new_id}", json={"is_admin": False}).status_code == 200

    # Delete the guest: their broker keys go too.
    assert client.delete(f"/api/admin/users/{guest['id']}").status_code == 200
    names = [u["username"] for u in client.get("/api/admin/users").json()["users"]]
    assert names == ["owner", "neighbour"]

    # The new password works; the deleted login does not.
    client.post("/api/auth/logout")
    _login(client, "neighbour", "changed123")
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "guest", "password": "guestpass1"}).status_code == 401


def test_non_admins_are_refused(client):
    _login(client, "guest", "guestpass1")
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/admin/users", json={"username": "x1234", "display_name": "x", "password": "longenough"}).status_code == 403
    assert client.delete("/api/admin/users/1").status_code == 403
