"""Alpaca takes option market orders in the regular session only. Outside
it the ticket refuses up front -- with the reason -- instead of passing the
order on for the broker to reject with "market orders not allowed"."""

import asyncio
from datetime import date, datetime

import pytest

from app.options import service as service_module
from app.options.models import SpreadTicket
from app.options.service import MARKET_ORDER_OUTSIDE_SESSION, market_order_refusal
from app.services.market_clock import ET
from app.trading.errors import OrderRejected
from tests.test_single_leg_submit import EXPIRY, _leg, _resolved, _service


@pytest.mark.parametrize(
    "order_type, account, now, refused",
    [
        ("market", "paper", datetime(2026, 9, 21, 10, 0, tzinfo=ET), False),  # Monday, in session
        ("market", "paper", datetime(2026, 9, 21, 16, 20, tzinfo=ET), True),  # twenty minutes after the close
        ("market", "live", datetime(2026, 9, 21, 8, 0, tzinfo=ET), True),  # premarket
        ("market", "paper", datetime(2026, 9, 19, 12, 0, tzinfo=ET), True),  # Saturday
        ("limit", "paper", datetime(2026, 9, 21, 16, 20, tzinfo=ET), False),  # a limit rests
        ("market", "sim", datetime(2026, 9, 21, 16, 20, tzinfo=ET), False),  # our own book
    ],
)
def test_when_a_market_order_is_refused(order_type, account, now, refused):
    reason = market_order_refusal(order_type, account, now)
    assert (reason == MARKET_ORDER_OUTSIDE_SESSION) is refused


def test_submit_refuses_before_anything_reaches_alpaca(monkeypatch):
    legs = [_leg("SPY260918P00740000", "put", 740, "buy"), _leg("SPY260918P00745000", "put", 745, "sell")]
    resolved = _resolved("bull_put", legs, -1.2).model_copy(update={"order_type": "market", "options_level": 3})
    service, trading = _service(monkeypatch, resolved)
    monkeypatch.setattr(service_module, "current_session", lambda now=None: "postmarket")
    ticket = SpreadTicket(
        underlying="SPY", strategy="bull_put", expiry=EXPIRY, qty=2, long_strike=740, short_strike=745, order_type="market"
    )
    with pytest.raises(OrderRejected) as caught:
        asyncio.run(service.submit(ticket))
    assert "regular session" in str(caught.value)
    assert trading.requests == []


def test_a_limit_order_after_hours_still_goes_through(monkeypatch):
    legs = [_leg("SPY260918P00740000", "put", 740, "buy"), _leg("SPY260918P00745000", "put", 745, "sell")]
    resolved = _resolved("bull_put", legs, -1.2).model_copy(update={"options_level": 3})
    service, trading = _service(monkeypatch, resolved)
    monkeypatch.setattr(service_module, "current_session", lambda now=None: "postmarket")
    ticket = SpreadTicket(underlying="SPY", strategy="bull_put", expiry=EXPIRY, qty=2, long_strike=740, short_strike=745)
    asyncio.run(service.submit(ticket))
    assert len(trading.requests) == 1
