"""A close at Alpaca while an earlier close still rests: refused up front,
naming the order in the way, instead of the broker's "insufficient qty
available" -- seen live on a cash-secured put whose 25.11 buy-to-close
rested under a 25.16 ask while a market close was tried on top of it."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.options.models import CloseLeg, CloseSpreadRequest, SpreadLeg
from app.options.service import OptionsService, working_closes
from app.trading.errors import CloseAlreadyWorking, OrderRejected

EXPIRY = date(2028, 1, 21)
PUT = "SPY280121P00685000"
SHORT_745 = "SPY261218P00745000"
LONG_740 = "SPY261218P00740000"


def _resting_simple(order_id="o-rest", limit="25.11", intent="buy_to_close", qty="1", filled="0") -> dict:
    return {
        "id": order_id, "symbol": PUT, "qty": qty, "filled_qty": filled, "limit_price": limit,
        "position_intent": intent, "legs": None, "status": "new",
    }


def test_working_closes_reads_simple_and_mleg_orders():
    mleg = {
        "id": "o-mleg", "qty": "2", "filled_qty": "0", "limit_price": "-0.80", "legs": [
            {"symbol": SHORT_745, "position_intent": "buy_to_close", "ratio_qty": "1"},
            {"symbol": LONG_740, "position_intent": "sell_to_close", "ratio_qty": "1"},
        ],
    }
    committed = working_closes([_resting_simple(), mleg, _resting_simple("o-open", intent="sell_to_open")])
    assert committed[PUT] == [{"id": "o-rest", "contracts": 1, "limit_price": 25.11}]
    # The MLEG's signed limit is reported as a magnitude, per leg it closes.
    assert committed[SHORT_745] == [{"id": "o-mleg", "contracts": 2, "limit_price": 0.80}]
    assert committed[LONG_740][0]["id"] == "o-mleg"


def test_a_filled_order_commits_nothing():
    assert working_closes([_resting_simple(filled="1")]) == {}


class _Trading:
    def __init__(self, held: int):
        self.held = held
        self.requests = []

    def get_open_position(self, symbol):
        return SimpleNamespace(qty=str(-self.held))

    def submit_order(self, request):
        self.requests.append(request)
        return {"id": "o-new", "status": "accepted"}


def _service(monkeypatch, resting: list[dict], held: int = 1) -> tuple[OptionsService, _Trading]:
    trading = _Trading(held)
    settings = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s", trading_enabled=True)
    service = OptionsService(clients=None, settings=settings, chain_cache=object())  # type: ignore[arg-type]
    monkeypatch.setattr(OptionsService, "_trading", property(lambda self: trading))

    async def orders(self, status="closed"):
        assert status == "open"
        return resting

    async def priced_close(self, req):
        leg = SpreadLeg(
            symbol=PUT, kind="put", strike=685.0, expiry=EXPIRY, side="buy", position_intent="buy_to_close",
            bid=25.04, ask=25.16, mid=25.10,
        )
        return [leg], "debit", 25.10, 25.16

    monkeypatch.setattr(OptionsService, "orders", orders)
    monkeypatch.setattr(OptionsService, "_priced_close", priced_close)
    monkeypatch.setattr("app.options.service.current_session", lambda now=None: "regular")
    return service, trading


def _close(order_type="market") -> CloseSpreadRequest:
    return CloseSpreadRequest(legs=[CloseLeg(symbol=PUT, qty=-1)], qty=1, order_type=order_type)


def test_a_market_close_on_top_of_a_resting_close_is_refused_naming_it(monkeypatch):
    service, trading = _service(monkeypatch, [_resting_simple()])
    with pytest.raises(CloseAlreadyWorking) as exc:
        asyncio.run(service.close_spread(_close()))
    assert trading.requests == []
    assert "25.11" in exc.value.message
    detail = exc.value.to_detail()
    assert detail["code"] == "close_already_working"
    assert detail["working_orders"] == [{"id": "o-rest", "contracts": 1, "limit_price": 25.11, "symbols": [PUT]}]


def test_the_preview_names_the_resting_close_before_anyone_clicks(monkeypatch):
    service, _ = _service(monkeypatch, [_resting_simple()])
    preview = asyncio.run(service.preview_close(_close("limit")))
    assert [w["id"] for w in preview["working_orders"]] == ["o-rest"]


def test_nothing_in_the_way_places_the_close(monkeypatch):
    service, trading = _service(monkeypatch, [_resting_simple("o-open", intent="sell_to_open")])
    order = asyncio.run(service.close_spread(_close()))
    assert order["id"] == "o-new" and len(trading.requests) == 1
    assert asyncio.run(service.preview_close(_close("limit")))["working_orders"] == []


def test_room_left_beside_a_partial_resting_close_is_allowed(monkeypatch):
    # Two held, one already on its way out: closing the other one is fine.
    service, trading = _service(monkeypatch, [_resting_simple()], held=2)
    asyncio.run(service.close_spread(_close()))
    assert len(trading.requests) == 1


def test_closing_more_than_is_held_is_refused_plainly(monkeypatch):
    service, trading = _service(monkeypatch, [], held=0)
    with pytest.raises(OrderRejected) as exc:
        asyncio.run(service.close_spread(_close()))
    assert not isinstance(exc.value, CloseAlreadyWorking)
    assert "only 0 contracts held" in exc.value.message
    assert trading.requests == []
