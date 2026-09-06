"""The paper account behind a playbook: Alpaca's orders and activities
translated into the simulated book's row shape (alpaca_adapter), the
journal fills an assignment or expiration stands for (fills_from_activities),
a runner tick over a fake paper service, the paper loop, and the router
accepting `account=paper` while refusing live."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.options.occ import format_occ
from app.playbooks import loader
from app.playbooks.alpaca_adapter import book_order_from_activity, book_order_from_alpaca, parse_stamp
from app.playbooks.paper_loop import tick_paper_campaigns
from app.playbooks.runner import PlaybookRunner, order_events
from app.playbooks.store import PlaybookStore
from app.trading.trades import Fill, fills_from_activities, round_trips
from tests.test_playbooks_core import E_MID, E_SHORT, NOW, _Service

PUT = format_occ("XYZ", E_MID, "put", 95.0)
PUT_LATER = format_occ("XYZ", date(2026, 10, 30), "put", 95.0)
CALL = format_occ("XYZ", E_MID, "call", 105.0)


# --- stamps ---------------------------------------------------------------------


def test_stamps_with_z_and_offsets_compare_as_instants():
    z = parse_stamp("2026-09-08T15:00:00.123Z")
    plus = parse_stamp("2026-09-08T15:00:00.123+00:00")
    naive = parse_stamp("2026-09-08T15:00:00.123")
    assert z == plus == naive and z.tzinfo is not None
    assert parse_stamp("2026-09-08T15:00:01Z") > parse_stamp("2026-09-08T15:00:00+00:00")
    assert parse_stamp(None) is None and parse_stamp("nope") is None


# --- orders ---------------------------------------------------------------------------


def _alpaca_single(symbol, side, intent, price, qty=1, status="filled", order_id="a1"):
    return {
        "id": order_id, "symbol": symbol, "side": side, "position_intent": intent, "status": status, "order_class": "simple",
        "qty": str(qty), "filled_qty": str(qty), "filled_avg_price": str(price), "filled_at": "2026-09-08T14:31:00.5Z",
        "submitted_at": "2026-09-08T14:30:00Z", "legs": None,
    }


def test_a_sold_put_a_close_and_a_roll_translate_to_book_rows():
    sold = book_order_from_alpaca(_alpaca_single(PUT, "sell", "sell_to_open", 1.85))
    assert sold["strategy"] == "cash_secured_put" and sold["underlying"] == "XYZ" and sold["qty"] == 1
    assert sold["net_fill_price"] == -1.85 and sold["legs"][0]["position_intent"] == "sell_to_open"
    assert sold["filled_at"].startswith("2026-09-08T14:31:00.500000+00:00")
    (ev,) = order_events({"symbol": "XYZ"}, sold)
    assert ev["kind"] == "sold_put" and ev["cash_delta"] == 185.0

    call = book_order_from_alpaca(_alpaca_single(CALL, "sell", "sell_to_open", 0.9, qty=2, order_id="a2"))
    assert call["strategy"] == "covered_call" and order_events({"symbol": "XYZ"}, call)[0]["cash_delta"] == 180.0

    closed = book_order_from_alpaca(_alpaca_single(PUT, "buy", "buy_to_close", 0.40, order_id="a3"))
    assert closed["strategy"] == "close" and closed["net_fill_price"] == 0.40
    assert order_events({"symbol": "XYZ"}, closed)[0]["kind"] == "closed"

    roll = book_order_from_alpaca(
        {
            "id": "a4", "symbol": None, "status": "filled", "order_class": "mleg", "qty": "1", "filled_qty": "1", "filled_avg_price": None,
            "filled_at": "2026-09-09T15:00:00Z", "submitted_at": "2026-09-09T14:59:00Z",
            "legs": [
                {"symbol": PUT, "side": "buy", "position_intent": "buy_to_close", "ratio_qty": "1", "filled_avg_price": "0.40", "filled_qty": "1"},
                {"symbol": PUT_LATER, "side": "sell", "position_intent": "sell_to_open", "ratio_qty": "1", "filled_avg_price": "2.10", "filled_qty": "1"},
            ],
        }
    )
    assert roll["strategy"] == "roll" and roll["net_fill_price"] == -1.7
    (ev,) = order_events({"symbol": "XYZ"}, roll)
    assert ev["kind"] == "rolled" and ev["occ"] == PUT_LATER and ev["cash_delta"] == 170.0 and "closed" in ev["note"]

    # Not filled, not an option, no price: nothing.
    assert book_order_from_alpaca(_alpaca_single(PUT, "sell", "sell_to_open", 1.85, status="new")) is None
    assert book_order_from_alpaca(_alpaca_single("XYZ", "buy", None, 100.0)) is None
    assert book_order_from_alpaca({**_alpaca_single(PUT, "sell", "sell_to_open", 1.85), "filled_avg_price": None}) is None


def test_activities_become_settlement_rows():
    assigned = book_order_from_activity({"id": "act1", "activity_type": "OPASN", "date": "2026-10-09", "symbol": PUT, "qty": "-1", "description": "Option assignment"})
    assert assigned["strategy"] == "assigned" and assigned["status"] == "assigned" and assigned["qty"] == 1
    (ev,) = order_events({"symbol": "XYZ"}, assigned)
    assert ev["kind"] == "assigned" and ev["qty"] == 100 and ev["price"] == 95.0
    # The settlement moment: 16:00 ET on the activity's date.
    assert parse_stamp(assigned["filled_at"]).astimezone(timezone.utc).hour in (20, 21)

    called = book_order_from_activity({"id": "act2", "activity_type": "OPASN", "date": "2026-10-09", "symbol": CALL, "qty": 2})
    assert called["strategy"] == "called_away" and order_events({"symbol": "XYZ"}, called)[0]["qty"] == -200

    expired = book_order_from_activity({"id": "act3", "activity_type": "OPEXP", "date": "2026-10-09", "symbol": PUT, "qty": 1})
    assert expired["strategy"] == "expiry" and order_events({"symbol": "XYZ"}, expired)[0]["kind"] == "expired"

    # The stock side of an assignment, dividends, anything without a contract: skipped.
    assert book_order_from_activity({"id": "act4", "activity_type": "OPASN", "date": "2026-10-09", "symbol": "XYZ", "qty": 100}) is None
    assert book_order_from_activity({"id": "act5", "activity_type": "DIV", "date": "2026-10-09", "symbol": "XYZ"}) is None


# --- the journal --------------------------------------------------------------------


def test_an_assignment_closes_the_short_puts_round_trip_at_zero():
    sold = Fill(symbol=PUT, side="sell", qty=1, price=1.85, at=datetime(2026, 9, 8, 14, 31, tzinfo=timezone.utc), order_id="a1")
    activities = [{"id": "act1", "activity_type": "OPASN", "date": E_MID.isoformat(), "symbol": PUT, "qty": -1}]

    extra = fills_from_activities(activities, [sold])

    assert len(extra) == 1 and extra[0].side == "buy" and extra[0].price == 0.0 and extra[0].qty == 1 and extra[0].order_id == "act1"
    closed, still_open = round_trips([sold, *extra])
    assert len(closed) == 1 and closed[0].symbol == PUT and closed[0].pnl == pytest.approx(185.0)
    assert PUT not in still_open
    # Already flat, or unknown to the fills: nothing is invented.
    assert fills_from_activities(activities, [sold, *extra]) == []
    assert fills_from_activities([{"id": "x", "activity_type": "OPEXP", "date": E_MID.isoformat(), "symbol": CALL, "qty": 1}], [sold]) == []


# --- a paper tick ----------------------------------------------------------------------


class _PaperService(_Service):
    """The fake sim service with Alpaca-shaped orders and activities."""

    def __init__(self):
        super().__init__()
        self.alpaca_orders: list[dict] = []
        self.acts: list[dict] = []
        self.activities_after: list = []

    async def orders(self, status="open"):
        return list(self.alpaca_orders) if status == "closed" else []

    async def activities(self, after=None):
        self.activities_after.append(after)
        return list(self.acts)


@pytest.fixture
def store(tmp_path):
    s = PlaybookStore(str(tmp_path / "pb.sqlite3"))
    asyncio.run(s.init_schema())
    return s


def test_a_paper_campaign_reconciles_alpaca_orders_and_an_assignment(store):
    runner = PlaybookRunner(store)
    wheel = loader.get_playbook("wheel")
    campaign = asyncio.run(store.create(1, "paper", "XYZ", "wheel", wheel.resolve_params({}), now=NOW))
    service = _PaperService()

    result = asyncio.run(runner.run_one(campaign, service, now=NOW, force=True))
    assert result["proposal"]["kind"] == "sell_put"
    assert service.activities_after == [campaign["created_at"]]

    # The user sells the put at Alpaca; a filled order appears.
    service.alpaca_orders.append(_alpaca_single(PUT, "sell", "sell_to_open", 1.85, order_id="a1") | {"filled_at": (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")})
    service.marks.append({"symbol": PUT, "qty": "-1", "avg_entry_price": "1.85", "current_price": "1.60"})
    result = asyncio.run(runner.run_one(campaign, service, now=NOW + timedelta(minutes=2)))
    assert [e["kind"] for e in asyncio.run(store.events(campaign["id"]))] == ["sold_put"]
    assert result["premiums_collected"] == 185.0 and result["phase"] == "short_put"
    assert parse_stamp(result["orders_cursor"]) == NOW + timedelta(minutes=1)

    # Assigned at expiry: an OPASN activity, the put gone, 100 shares held.
    service.acts.append({"id": "act1", "activity_type": "OPASN", "date": E_MID.isoformat(), "symbol": PUT, "qty": -1, "description": "Option assignment"})
    service.marks.clear()
    service.shares = {"qty": 100.0, "avg_entry_price": 95.0}
    later = datetime.combine(E_MID + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    result = asyncio.run(runner.run_one(campaign, service, now=later))
    kinds = [e["kind"] for e in asyncio.run(store.events(campaign["id"]))]
    assert kinds == ["sold_put", "assigned"]
    assert result["shares"] == 100 and result["cost_basis"] == pytest.approx(95.0 - 1.85) and result["proposal"]["kind"] == "sell_call"
    # A paper campaign never executes, whatever the switch says.
    asyncio.run(store.update(campaign["id"], auto_execute=True))
    asyncio.run(runner.run_one(campaign, service, now=later + timedelta(minutes=1), force=True))
    assert service.submitted == []
    # The activity is recorded once.
    asyncio.run(runner.run_one(campaign, service, now=later + timedelta(minutes=2), force=True))
    assert len(asyncio.run(store.events(campaign["id"]))) == 2


def test_the_paper_loop_ticks_each_user_with_a_campaign(store, monkeypatch):
    import app.playbooks.paper_loop as paper_loop

    wheel = loader.get_playbook("wheel")
    asyncio.run(store.create(1, "paper", "XYZ", "wheel", wheel.resolve_params({}), now=NOW))
    asyncio.run(store.create(2, "paper", "ABC", "wheel", wheel.resolve_params({}), now=NOW))
    asyncio.run(store.create(3, "sim", "ABC", "wheel", wheel.resolve_params({}), now=NOW))
    services: dict[int, _PaperService] = {1: _PaperService(), 2: _PaperService()}
    seen: list = []

    async def fake_service_for(clients, settings, engine, chain_cache, resolver, user, account):
        seen.append((user and user["id"], account))
        return services.get(user["id"]) if user else None

    class _Users:
        async def list_users(self):
            return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(paper_loop, "_service_for", fake_service_for)
    runner = PlaybookRunner(store)

    ticked = asyncio.run(tick_paper_campaigns(None, None, store, runner, None, None, resolver=object(), user_store=_Users()))

    assert ticked == 2 and sorted(seen) == [(1, "paper"), (2, "paper")]
    for campaign in asyncio.run(store.all_active("paper")):
        assert campaign["proposal"] is not None
    # The sim campaign is the sim loop's business.
    assert asyncio.run(store.all_active("sim"))[0]["proposal"] is None


# --- the router ---------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    from app.auth.dependency import get_current_user
    from app.routers import playbooks

    store = PlaybookStore(str(tmp_path / "pb.sqlite3"))
    asyncio.run(store.init_schema())
    services = {"sim": _Service(), "paper": _PaperService()}

    app = FastAPI()
    app.include_router(playbooks.router)
    app.state.playbook_store = store
    app.state.playbook_runner = PlaybookRunner(store)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "t"}

    async def fake_sim(request, user):
        return services["sim"]

    async def fake_paper(request, user):
        return services["paper"]

    playbooks._sim_service = fake_sim
    playbooks._paper_service = fake_paper
    return TestClient(app), store, services


def test_the_router_accepts_paper_and_refuses_live(api):
    client, _store, services = api

    resp = client.post("/api/trading/options/playbooks/campaigns", json={"account": "paper", "symbol": "XYZ", "playbook": "wheel"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["account"] == "paper" and resp.json()["proposal"]["kind"] == "sell_put"
    assert services["paper"].activities_after  # the paper service was the one asked

    listed = client.get("/api/trading/options/playbooks/campaigns", params={"account": "paper"}).json()["campaigns"]
    assert [c["account"] for c in listed] == ["paper"]
    assert client.get("/api/trading/options/playbooks/campaigns", params={"account": "sim"}).json()["campaigns"] == []

    live = client.post("/api/trading/options/playbooks/campaigns", json={"account": "live", "symbol": "XYZ", "playbook": "wheel"})
    assert live.status_code == 422 and live.json()["detail"]["code"] == "account_not_allowed"
