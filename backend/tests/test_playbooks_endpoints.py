"""The playbooks router over a real PlaybookStore in tmp_path, with the sim
service faked the way test_playbooks_core fakes it: scripts listing,
creating a campaign (with its first proposal), the sim-only rule, patching
status/params/auto_execute, notes."""

import asyncio
from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.playbooks.runner import PlaybookRunner
from app.playbooks.store import PlaybookStore
from tests.test_playbooks_core import _Service

NOW = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def api(tmp_path):
    from app.auth.dependency import get_current_user
    from app.routers import playbooks, trading_sim_options

    store = PlaybookStore(str(tmp_path / "pb.sqlite3"))
    asyncio.run(store.init_schema())
    service = _Service()

    app = FastAPI()
    app.include_router(playbooks.router)
    app.state.playbook_store = store
    app.state.playbook_runner = PlaybookRunner(store)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "t"}

    async def fake_service(request, user):
        return service

    playbooks._sim_service = fake_service  # the router imported the name; swap it for the fake
    return TestClient(app), store, service


def test_scripts_are_listed_with_their_params(api):
    client, _store, _service = api
    body = client.get("/api/trading/options/playbooks/scripts").json()
    wheel = next(p for p in body["playbooks"] if p["stem"] == "wheel")
    assert wheel["name"] == "Wheel" and any(p["name"] == "put_delta" and p["type"] == "float" for p in wheel["params"])
    assert body["errors"] == []


def test_a_campaign_is_created_with_its_first_proposal_and_listed(api):
    client, store, service = api

    resp = client.post("/api/trading/options/playbooks/campaigns", json={"symbol": "xyz", "playbook": "wheel", "params": {"put_delta": 0.25}})

    assert resp.status_code == 200, resp.text
    campaign = resp.json()
    assert campaign["symbol"] == "XYZ" and campaign["status"] == "active" and campaign["params"]["put_delta"] == 0.25
    assert campaign["proposal"]["kind"] == "sell_put" and campaign["proposal"]["ticket"]["underlying"] == "XYZ"
    assert [e["kind"] for e in campaign["events"]] == ["started"]

    listed = client.get("/api/trading/options/playbooks/campaigns", params={"account": "sim"}).json()["campaigns"]
    assert [c["id"] for c in listed] == [campaign["id"]]

    # A second campaign on the same symbol is refused; bad params and an unknown playbook too.
    assert client.post("/api/trading/options/playbooks/campaigns", json={"symbol": "XYZ", "playbook": "wheel"}).status_code == 422
    assert client.post("/api/trading/options/playbooks/campaigns", json={"symbol": "ABC", "playbook": "wheel", "params": {"nope": 1}}).status_code == 422
    assert client.post("/api/trading/options/playbooks/campaigns", json={"symbol": "ABC", "playbook": "condor_monthly"}).status_code == 422


def test_paper_is_refused_as_sim_only_for_now(api):
    client, _store, _service = api
    resp = client.post("/api/trading/options/playbooks/campaigns", json={"account": "paper", "symbol": "XYZ", "playbook": "wheel"})
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "sim_only"
    assert client.get("/api/trading/options/playbooks/campaigns", params={"account": "paper"}).status_code == 422


def test_patch_pauses_resumes_closes_and_switches(api):
    client, store, _service = api
    campaign = client.post("/api/trading/options/playbooks/campaigns", json={"symbol": "XYZ", "playbook": "wheel"}).json()
    url = f"/api/trading/options/playbooks/campaigns/{campaign['id']}"

    paused = client.patch(url, json={"status": "paused"}).json()
    assert paused["status"] == "paused" and paused["events"][-1]["kind"] == "paused"
    resumed = client.patch(url, json={"status": "active", "params": {"qty": 2}, "auto_execute": True}).json()
    assert resumed["status"] == "active" and resumed["params"]["qty"] == 2 and resumed["auto_execute"] is True
    assert client.patch(url, json={"params": {"qty": 0}}).status_code == 422

    noted = client.post(f"{url}/note", json={"note": "watching the 95 put"}).json()
    assert noted["events"][-1]["kind"] == "manual_note" and noted["events"][-1]["note"] == "watching the 95 put"

    recomputed = client.post(f"{url}/propose").json()
    assert recomputed["proposal"]["kind"] in ("sell_put", "hold")

    closed = client.patch(url, json={"status": "closed"}).json()
    assert closed["status"] == "closed" and closed["closed_at"] is not None
    assert client.patch(url, json={"status": "active"}).status_code == 422
    assert client.get("/api/trading/options/playbooks/campaigns").json()["campaigns"] == []
    assert len(client.get("/api/trading/options/playbooks/campaigns", params={"include_closed": "true"}).json()["campaigns"]) == 1
    assert client.get(f"/api/trading/options/playbooks/campaigns/nope/propose").status_code in (404, 405)
