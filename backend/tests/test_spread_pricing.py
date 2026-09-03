from datetime import date

import pytest

from app.options.models import SpreadLeg
from app.options.pricing import (
    alpaca_limit,
    assert_spread_within_limits,
    marketable_close_limit,
    net_price,
    spread_risk,
)
from app.trading.errors import OrderRejected


def _leg(side, bid, ask, mid=None, **kw) -> SpreadLeg:
    return SpreadLeg(
        symbol="SPY260918P00740000",
        kind="put",
        strike=740,
        expiry=date(2026, 9, 18),
        side=side,
        position_intent="buy_to_open" if side == "buy" else "sell_to_open",
        bid=bid,
        ask=ask,
        mid=mid if mid is not None else (None if bid is None or ask is None else round((bid + ask) / 2, 4)),
        **kw,
    )


# --- net price ----------------------------------------------------------------


def test_net_price_mid_is_signed_buy_minus_sell():
    legs = [_leg("buy", 1.0, 1.2), _leg("sell", 2.0, 2.2)]
    assert net_price(legs, "mid") == pytest.approx(1.1 - 2.1)  # a credit: negative


def test_net_price_natural_crosses_the_market():
    legs = [_leg("buy", 1.0, 1.2), _leg("sell", 2.0, 2.2)]
    # Buy at the ask (1.2), sell at the bid (2.0): receive 0.80, the worst case.
    assert net_price(legs, "natural") == pytest.approx(1.2 - 2.0)


def test_net_price_is_none_with_a_missing_quote():
    assert net_price([_leg("buy", None, None), _leg("sell", 2.0, 2.2)], "mid") is None
    assert net_price([_leg("buy", 1.0, None), _leg("sell", 2.0, 2.2)], "natural") is None


# --- risk math per strategy -------------------------------------------------


def test_bull_call_debit():
    risk = spread_risk("bull_call", (745, 750), 2.0, 1)
    assert risk.direction == "debit"
    assert risk.width == 5
    assert risk.max_loss == 200.0
    assert risk.max_profit == 300.0
    assert risk.breakevens == [747.0]
    assert risk.collateral == 200.0


def test_bear_put_debit_breakeven_is_below_the_long_strike():
    risk = spread_risk("bear_put", (750, 745), 1.5, 2)
    assert risk.max_loss == 300.0
    assert risk.max_profit == 700.0
    assert risk.breakevens == [748.5]


def test_bull_put_credit():
    risk = spread_risk("bull_put", (740, 745), 1.2, 1)
    assert risk.direction == "credit"
    assert risk.max_profit == 120.0
    assert risk.max_loss == 380.0
    assert risk.breakevens == [743.8]
    assert risk.collateral == 380.0


def test_bear_call_credit_breakeven_is_above_the_short_strike():
    risk = spread_risk("bear_call", (755, 750), 0.8, 3)
    assert risk.max_profit == 240.0
    assert risk.max_loss == 1260.0
    assert risk.breakevens == [750.8]


def test_iron_condor_uses_the_wider_wing():
    risk = spread_risk("iron_condor", (735, 740, 755, 765), 2.0, 1)
    assert risk.width == 10
    assert risk.max_profit == 200.0
    assert risk.max_loss == 800.0
    assert risk.breakevens == [738.0, 757.0]
    assert risk.collateral == 800.0


@pytest.mark.parametrize("price", [0, -1, 5, 6])
def test_price_must_be_positive_and_below_the_width(price):
    with pytest.raises(OrderRejected) as exc:
        spread_risk("bull_call", (745, 750), price, 1)
    assert exc.value.field == "limit_price"


# --- limits and sign -----------------------------------------------------------


def test_alpaca_limit_sign_convention():
    assert alpaca_limit("debit", 1.234) == 1.23
    assert alpaca_limit("credit", 1.234) == -1.23


def test_marketable_close_limit_steps_toward_natural_and_caps_there():
    # Receiving: ask a little less than mid, never less than the natural.
    assert marketable_close_limit("credit", 1.50, 1.30, 0.05) == 1.45
    assert marketable_close_limit("credit", 1.50, 1.48, 0.05) == 1.48
    # Paying: offer a little more than mid, never more than the natural.
    assert marketable_close_limit("debit", 1.50, 1.70, 0.05) == 1.55
    assert marketable_close_limit("debit", 1.50, 1.52, 0.05) == 1.52
    assert marketable_close_limit("credit", 0.03, None, 0.05) == 0.01


def test_limits_refuse_contracts_notional_and_buying_power():
    ok = dict(qty=2, collateral=800.0, options_buying_power=5_000.0, max_contracts=5, max_notional=25_000.0)
    assert_spread_within_limits(**ok)
    with pytest.raises(OrderRejected):
        assert_spread_within_limits(**{**ok, "qty": 6})
    with pytest.raises(OrderRejected):
        assert_spread_within_limits(**{**ok, "collateral": 30_000.0})
    with pytest.raises(OrderRejected):
        assert_spread_within_limits(**{**ok, "options_buying_power": 500.0})
    assert_spread_within_limits(**{**ok, "options_buying_power": None})


def test_long_call_has_unlimited_profit_and_premium_at_risk():
    r = spread_risk("long_call", (750,), 2.5, 2)
    assert r.direction == "debit" and r.width == 0.0
    assert r.max_profit is None
    assert r.max_loss == 500.0 and r.collateral == 500.0
    assert r.breakevens == [752.5]


def test_long_put_profit_is_capped_at_the_strike():
    r = spread_risk("long_put", (740,), 3.0, 1)
    assert r.max_profit == 73700.0 and r.max_loss == 300.0
    assert r.breakevens == [737.0]
    with pytest.raises(OrderRejected):
        spread_risk("long_put", (740,), 740.0, 1)
