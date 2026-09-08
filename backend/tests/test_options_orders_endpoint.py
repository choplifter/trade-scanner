"""The Alpaca options router's order list and cancel -- what the Open
spreads tab's Working packages read in Paper and Live. A fake service in
place of the broker, the way test_playbooks_endpoints does it."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.trading.errors import LiveConfirmationRequired


class _Service:
    def __init__(self):
        self.listed: list[str] = []
        self.cancelled: list[tuple[str, str | None]] = []
        self.refuse_without_confirm = False

    async def orders(self, status="closed"):
        self.listed.append(status)
        return [
            {"id": "o1", "status": "new", "symbol": "SPY260918P00745000", "side": "sell", "qty": "1", "limit_price": "1.20", "legs": None},
            {
                "id": "o2", "status": "accepted", "symbol": None, "side": None, "qty": "2", "limit_price": "0.85",
                "legs": [{"symbol": "SPY260918P00740000", "side": "buy"}, {"symbol": "SPY260918P00745000", "side": "sell"}],
            },
        ]

    async def cancel(self, order_id, confirm=None):
        if self.refuse_without_confirm and confirm != "LIVE":
            raise LiveConfirmationRequired("Type LIVE to confirm")
        self.cancelled.append((order_id, confirm))


@pytest.fixture
def api():
    from app.auth.dependency import get_current_user
    from app.routers import trading_options

    service = _Service()
    app = FastAPI()
    app.include_router(trading_options.router, prefix="/api/trading/options")
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "t"}
    app.dependency_overrides[trading_options._service] = lambda: service
    return TestClient(app), service


def test_resting_option_orders_are_listed_open_by_default(api):
    client, service = api
    body = client.get("/api/trading/options/orders").json()
    assert body["status"] == "open" and service.listed == ["open"]
    assert [o["id"] for o in body["orders"]] == ["o1", "o2"]
    assert body["orders"][1]["legs"][0]["symbol"] == "SPY260918P00740000"
    client.get("/api/trading/options/orders?status=all")
    assert service.listed[-1] == "all"


def test_cancel_passes_the_typed_confirmation_through(api):
    client, service = api
    assert client.delete("/api/trading/options/orders/o1").json() == {"cancelled": "o1"}
    assert service.cancelled == [("o1", None)]
    service.refuse_without_confirm = True
    refused = client.delete("/api/trading/options/orders/o2")
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "live_confirmation_required"
    ok = client.delete("/api/trading/options/orders/o2", headers={"X-Live-Confirm": "LIVE"})
    assert ok.status_code == 200 and service.cancelled[-1] == ("o2", "LIVE")
