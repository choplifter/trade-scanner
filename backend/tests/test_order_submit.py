"""The write path: guard first, then translation of the broker's answers."""

import pytest

from app.core.config import Settings
from app.trading.errors import OrderRejected, TradingDisabled, rejection_from_api_error
from app.trading.models import ResolvedOrder
from app.trading.service import OrderService, _build_request


def _resolved(**overrides) -> ResolvedOrder:
    base = dict(
        symbol="AAPL", side="buy", order_type="market", time_in_force="day",
        order_class="simple", qty=10, entry_reference=100.0, notional=1000.0,
    )
    base.update(overrides)
    return ResolvedOrder(**base)


@pytest.mark.asyncio
async def test_submit_refuses_before_touching_the_broker():
    """The guard runs first, so a refusal costs no request and cannot be
    reached by a caller that skipped the route."""
    service = OrderService(
        clients=None,  # type: ignore[arg-type]
        settings=Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s", trading_enabled=False),
    )
    with pytest.raises(TradingDisabled):
        # clients is None -- reaching the SDK at all would AttributeError,
        # so passing proves nothing was attempted.
        await service.submit(None)  # type: ignore[arg-type]


def test_simple_order_carries_no_legs():
    request = _build_request(_resolved())
    assert request.take_profit is None
    assert request.stop_loss is None


def test_bracket_builds_both_legs():
    request = _build_request(
        _resolved(order_class="bracket", take_profit_price=110.0, stop_loss_price=95.0)
    )
    assert request.take_profit.limit_price == 110.0
    assert request.stop_loss.stop_price == 95.0


def test_limit_order_carries_its_price():
    request = _build_request(_resolved(order_type="limit", limit_price=99.0))
    assert request.limit_price == 99.0


def test_client_order_id_is_passed_through_for_idempotent_retries():
    """Alpaca rejects a duplicate id, so a retry after a timeout is the same
    order rather than a second one."""
    assert _build_request(_resolved(client_order_id="abc-123")).client_order_id == "abc-123"


class _FakeAPIError(Exception):
    def __init__(self, status_code, payload):
        super().__init__(payload)
        self.status_code = status_code
        self._payload = payload

    @property
    def message(self):
        import json

        return json.loads(self._payload)["message"]


def test_broker_rejections_become_typed_refusals():
    rejection = rejection_from_api_error(_FakeAPIError(403, '{"message": "insufficient buying power"}'))
    assert isinstance(rejection, OrderRejected)
    assert "insufficient buying power" in rejection.message


def test_a_non_json_error_body_does_not_raise_while_handling_the_error():
    """APIError.message does an unguarded json.loads then ["message"]. An
    HTML error page would otherwise raise from inside the handler and
    replace a useful rejection with a traceback."""
    rejection = rejection_from_api_error(_FakeAPIError(422, "<html>gateway timeout</html>"))
    assert isinstance(rejection, OrderRejected)
    assert rejection.message  # fell back to str(exc) rather than exploding


def test_unexpected_status_codes_are_re_raised_not_swallowed():
    """A 500 from the broker is a fault, not an answer -- it must surface as
    a 502 rather than be presented to the user as a rejection they caused."""
    assert rejection_from_api_error(_FakeAPIError(500, '{"message": "boom"}')) is None
