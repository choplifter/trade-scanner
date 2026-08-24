"""Moving a stop and selling part of a position.

The two new write paths share the shape of test_order_submit: the guard is
proven with clients=None (any SDK access would AttributeError), the
mappings and refusals are pure, and the SDK sequence is recorded by a stub.
The case that matters most is the partial close's re-arm: a sale that
quietly drops the remainder's stop is worse than one that fails.
"""

import asyncio
import json

import pytest

from app.core.config import Settings
from app.trading.errors import OrderRejected, TradingDisabled
from app.trading.service import (
    OrderService,
    _validate_stop_replacement,
    rearm_requests,
)


def _settings(**overrides):
    values = {
        "alpaca_api_key_id": "key",
        "alpaca_api_secret_key": "secret",
        "trading_enabled": True,
        "alpaca_paper": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


# --- the guard ------------------------------------------------------------


def test_replace_stop_is_guarded_before_anything_else():
    service = OrderService(clients=None, settings=_settings(trading_enabled=False))

    with pytest.raises(TradingDisabled):
        asyncio.run(service.replace_stop("id", "AAA", 10.0))


def test_partial_close_is_guarded_before_anything_else():
    service = OrderService(clients=None, settings=_settings(trading_enabled=False))

    with pytest.raises(TradingDisabled):
        asyncio.run(service.close_position("AAA", qty=5))


# --- refusals, pure -------------------------------------------------------


def _stop_order(**overrides):
    order = {
        "id": "abc",
        "symbol": "AAA",
        "side": "sell",
        "order_type": "stop",
        "status": "held",
        "stop_price": "9.50",
        "time_in_force": "gtc",
    }
    order.update(overrides)
    return order


def test_a_working_stop_on_the_right_symbol_passes():
    _validate_stop_replacement(_stop_order(), "AAA", 9.0, reference_price=10.0)


def test_the_wrong_symbol_is_refused():
    with pytest.raises(OrderRejected) as exc:
        _validate_stop_replacement(_stop_order(symbol="BBB"), "AAA", 9.0, 10.0)
    assert exc.value.field == "order_id"


def test_a_filled_order_cannot_be_moved():
    with pytest.raises(OrderRejected):
        _validate_stop_replacement(_stop_order(status="filled"), "AAA", 9.0, 10.0)


def test_a_limit_order_is_not_a_stop():
    with pytest.raises(OrderRejected):
        _validate_stop_replacement(_stop_order(order_type="limit"), "AAA", 9.0, 10.0)


def test_a_sell_stop_above_the_market_would_trigger_instantly():
    with pytest.raises(OrderRejected) as exc:
        _validate_stop_replacement(_stop_order(), "AAA", 10.5, reference_price=10.0)
    assert exc.value.field == "stop_price"


def test_a_buy_stop_below_the_market_would_trigger_instantly():
    with pytest.raises(OrderRejected):
        _validate_stop_replacement(_stop_order(side="buy"), "AAA", 9.5, reference_price=10.0)


def test_an_unknowable_reference_price_does_not_block_the_move():
    """Same asymmetry as resolve_ticket: Alpaca still validates on its side,
    and refusing on ignorance would freeze the stop exactly when the feed
    hiccups."""
    _validate_stop_replacement(_stop_order(), "AAA", 10.5, reference_price=None)


# --- the re-arm mapping, pure ---------------------------------------------


def test_the_stop_comes_back_for_the_remaining_quantity():
    requests = rearm_requests([_stop_order()], remaining_qty=60)

    assert len(requests) == 1
    assert type(requests[0]).__name__ == "StopOrderRequest"
    assert requests[0].qty == 60
    assert requests[0].stop_price == 9.5


def test_a_take_profit_limit_comes_back_too():
    exits = [_stop_order(), _stop_order(order_type="limit", limit_price="14.80", stop_price=None)]

    requests = rearm_requests(exits, remaining_qty=60)

    assert [type(r).__name__ for r in requests] == ["StopOrderRequest", "LimitOrderRequest"]
    assert requests[1].limit_price == 14.8


def test_a_stop_limit_degrades_to_a_plain_stop():
    """Its limit leg was tuned to the old quantity's fill risk."""
    exits = [_stop_order(order_type="stop_limit", limit_price="9.40")]

    requests = rearm_requests(exits, remaining_qty=60)

    assert type(requests[0]).__name__ == "StopOrderRequest"


def test_nothing_remaining_rearms_nothing():
    assert rearm_requests([_stop_order()], remaining_qty=0) == []


def test_an_exit_without_a_price_produces_nothing_not_a_guess():
    assert rearm_requests([_stop_order(stop_price=None)], remaining_qty=60) == []


# --- the SDK sequence, stubbed --------------------------------------------


class _StubTradingClient:
    def __init__(self, orders=None, position_qty="100"):
        self._orders = orders or []
        self._position_qty = position_qty
        self.cancelled: list[str] = []
        self.submitted: list = []
        self.replaced: list[tuple] = []
        self.closed: list[tuple] = []

    def get_orders(self, request):
        return self._orders

    def get_order_by_id(self, order_id):
        for order in self._orders:
            if order.get("id") == order_id:
                return order
        raise _FakeAPIError(422, {"message": "order not found"})

    def get_open_position(self, symbol):
        return type("P", (), {"qty": self._position_qty})()

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)

    def submit_order(self, request):
        self.submitted.append(request)
        return {"id": "new", "order_type": type(request).__name__.replace("OrderRequest", "").lower()}

    def replace_order_by_id(self, order_id, request):
        self.replaced.append((order_id, request))
        return {"id": order_id, "stop_price": str(request.stop_price)}

    def close_position(self, symbol, close_options=None):
        self.closed.append((symbol, close_options))
        return {"id": "close", "symbol": symbol}


class _FakeAPIError(Exception):
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        super().__init__(payload.get("message", ""))

    @property
    def message(self):
        return json.dumps(self._payload)


def _service(client):
    clients = type("C", (), {"trading": client, "feed": "iex"})()
    return OrderService(clients=clients, settings=_settings())


def test_partial_close_cancels_sells_and_rearms_in_that_order():
    client = _StubTradingClient(orders=[_stop_order()], position_qty="100")
    service = _service(client)

    result = asyncio.run(service.close_position("AAA", qty=40))

    assert client.cancelled == ["abc"]
    assert client.closed[0][0] == "AAA"
    assert client.closed[0][1].qty == "40"
    assert len(client.submitted) == 1
    assert client.submitted[0].qty == 60
    assert result["stop_lost"] is False
    assert len(result["rearmed_orders"]) == 1


def test_selling_everything_is_a_full_close_without_rearm():
    client = _StubTradingClient(orders=[_stop_order()], position_qty="100")
    service = _service(client)

    result = asyncio.run(service.close_position("AAA", qty=100))

    assert client.closed[0][1] is None
    assert client.submitted == []
    assert "rearmed_orders" not in result


def test_a_failed_stop_rearm_is_loud():
    client = _StubTradingClient(orders=[_stop_order()], position_qty="100")
    client.submit_order = lambda request: (_ for _ in ()).throw(RuntimeError("down"))
    service = _service(client)

    result = asyncio.run(service.close_position("AAA", qty=40))

    assert result["stop_lost"] is True
    assert result["rearmed_orders"] == []


def test_replace_stop_calls_the_sdk_with_the_new_price():
    client = _StubTradingClient(orders=[_stop_order()])
    service = _service(client)

    result = asyncio.run(service.replace_stop("abc", "AAA", 9.0))

    assert client.replaced[0][0] == "abc"
    assert client.replaced[0][1].stop_price == 9.0
    assert result["stop_price"] == "9.0"
