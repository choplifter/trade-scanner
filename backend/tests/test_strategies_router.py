"""The strategy switch endpoints.

Mounted on a bare app rather than the real one: the router touches only the
loader and the switch file, and the real app's lifespan wants credentials
and a universe. The loader is pointed at a scratch directory for the same
reason test_strategy_loader does it -- these assert the endpoints'
behaviour, not whichever strategies happen to ship.
"""

import textwrap

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import strategies as strategies_router
from app.strategies import loader as loader_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_mod, "_DIR", tmp_path)
    (tmp_path / "rule.py").write_text(
        textwrap.dedent(
            """
            NAME = "Test Rule"

            def evaluate(ctx):
                return None
            """
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(strategies_router.router)
    return TestClient(app)


def test_the_listing_names_each_strategy_with_its_switch(client):
    body = client.get("/api/strategies").json()

    assert body["errors"] == []
    assert body["strategies"] == [
        {"name": "Test Rule", "filename": "rule.py", "stem": "rule", "enabled": True}
    ]


def test_switching_off_persists_and_reports_back(client):
    body = client.post("/api/strategies/rule", json={"enabled": False}).json()

    assert body["strategies"][0]["enabled"] is False
    # And it stuck: a fresh GET reads the persisted file, not request state.
    assert client.get("/api/strategies").json()["strategies"][0]["enabled"] is False


def test_switching_back_on(client):
    client.post("/api/strategies/rule", json={"enabled": False})
    body = client.post("/api/strategies/rule", json={"enabled": True}).json()

    assert body["strategies"][0]["enabled"] is True


def test_the_listing_carries_the_measured_move_setting(client):
    assert client.get("/api/strategies").json()["measured_move_target"] is True


def test_the_measured_move_setting_toggles_and_persists(client):
    body = client.post("/api/strategies/settings/measured-move", json={"enabled": False}).json()

    assert body["measured_move_target"] is False
    assert client.get("/api/strategies").json()["measured_move_target"] is False
    # And the strategies were untouched by it.
    assert body["strategies"][0]["enabled"] is True


def test_an_unknown_stem_is_a_404_not_an_orphan_entry(client):
    """Persisting a key no file owns would silently park the next strategy
    that happens to take that name."""
    res = client.post("/api/strategies/nope", json={"enabled": False})

    assert res.status_code == 404
    assert client.get("/api/strategies").json()["strategies"][0]["enabled"] is True
