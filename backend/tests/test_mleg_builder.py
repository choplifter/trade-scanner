"""The multi-leg request, asserted against the real alpaca-py request
classes -- like test_order_submit's _build_request tests, no client needed."""

from datetime import date

from alpaca.trading.enums import OrderClass, OrderSide, OrderType, PositionIntent, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

from app.options.models import SpreadLeg
from app.options.service import build_mleg_request, build_single_leg_request

EXPIRY = date(2026, 9, 18)


def _leg(symbol, side, intent, kind="put", strike=740.0) -> SpreadLeg:
    return SpreadLeg(symbol=symbol, kind=kind, strike=strike, expiry=EXPIRY, side=side, position_intent=intent)


def test_credit_vertical_is_a_day_mleg_limit_with_a_negative_price():
    legs = [
        _leg("SPY260918P00740000", "buy", "buy_to_open"),
        _leg("SPY260918P00745000", "sell", "sell_to_open", strike=745.0),
    ]
    request = build_mleg_request(legs, 2, -1.25, "cid-1")
    assert isinstance(request, LimitOrderRequest)
    assert request.order_class is OrderClass.MLEG
    assert request.time_in_force is TimeInForce.DAY
    assert request.type is OrderType.LIMIT
    assert request.qty == 2
    assert request.limit_price == -1.25
    assert request.client_order_id == "cid-1"
    assert request.symbol is None and request.side is None
    assert [leg.symbol for leg in request.legs] == ["SPY260918P00740000", "SPY260918P00745000"]
    assert [leg.side for leg in request.legs] == [OrderSide.BUY, OrderSide.SELL]
    assert [leg.position_intent for leg in request.legs] == [
        PositionIntent.BUY_TO_OPEN,
        PositionIntent.SELL_TO_OPEN,
    ]
    assert all(leg.ratio_qty == 1 for leg in request.legs)


def test_iron_condor_has_four_legs_and_a_positive_close_is_a_debit():
    legs = [
        _leg("SPY260918P00735000", "buy", "buy_to_open", strike=735.0),
        _leg("SPY260918P00740000", "sell", "sell_to_open", strike=740.0),
        _leg("SPY260918C00755000", "sell", "sell_to_open", kind="call", strike=755.0),
        _leg("SPY260918C00760000", "buy", "buy_to_open", kind="call", strike=760.0),
    ]
    request = build_mleg_request(legs, 1, -2.0, None)
    assert len(request.legs) == 4
    assert request.client_order_id is None
    closing = [
        _leg("SPY260918P00735000", "sell", "sell_to_close", strike=735.0),
        _leg("SPY260918P00740000", "buy", "buy_to_close", strike=740.0),
    ]
    request = build_mleg_request(closing, 1, 0.9, None)
    assert request.limit_price == 0.9
    assert request.legs[1].position_intent is PositionIntent.BUY_TO_CLOSE


def test_single_leg_close_is_a_plain_option_limit_order():
    leg = _leg("SPY260918P00740000", "sell", "sell_to_close")
    request = build_single_leg_request(leg, 3, -0.55, "cid-2")
    assert request.order_class is not OrderClass.MLEG
    assert request.symbol == "SPY260918P00740000"
    assert request.side is OrderSide.SELL
    assert request.position_intent is PositionIntent.SELL_TO_CLOSE
    assert request.limit_price == 0.55  # magnitude: a single leg has no signed net
    assert request.qty == 3
    assert request.time_in_force is TimeInForce.DAY


def test_butterfly_body_carries_ratio_two_and_a_calendar_spans_two_expiries():
    from app.options.models import SpreadLeg as _SpreadLeg

    fly = [
        _leg("SPY260918C00745000", "buy", "buy_to_open", kind="call", strike=745.0),
        _SpreadLeg(symbol="SPY260918C00750000", kind="call", strike=750.0, expiry=EXPIRY, side="sell",
                   position_intent="sell_to_open", ratio_qty=2),
        _leg("SPY260918C00755000", "buy", "buy_to_open", kind="call", strike=755.0),
    ]
    request = build_mleg_request(fly, 1, 1.20, None)
    assert [leg.ratio_qty for leg in request.legs] == [1, 2, 1]
    assert request.limit_price == 1.20 and len(request.legs) == 3

    calendar = [
        _leg("SPY260918C00750000", "sell", "sell_to_open", kind="call", strike=750.0),
        _SpreadLeg(symbol="SPY261016C00750000", kind="call", strike=750.0, expiry=date(2026, 10, 16), side="buy",
                   position_intent="buy_to_open"),
    ]
    request = build_mleg_request(calendar, 1, 2.10, "cid-cal")
    assert [leg.symbol for leg in request.legs] == ["SPY260918C00750000", "SPY261016C00750000"]
    assert request.order_class is OrderClass.MLEG and request.limit_price == 2.10


def test_income_writes_are_plain_sell_to_open_orders():
    leg = _leg("SPY260918C00760000", "sell", "sell_to_open", kind="call", strike=760.0)
    request = build_single_leg_request(leg, 2, 1.5, None)
    assert request.symbol == "SPY260918C00760000" and request.qty == 2
    assert request.side is OrderSide.SELL and request.position_intent is PositionIntent.SELL_TO_OPEN
    assert request.limit_price == 1.5 and request.legs is None
