"""Ticket validation: everything that must hold before an order is built.

Split across the two layers it lives in -- pydantic for what the ticket says
about itself, resolve_ticket for what needs the account and the ceilings.
"""

import pytest
from pydantic import ValidationError

from app.trading.errors import OrderRejected
from app.trading.models import OrderTicket, RiskSizing, resolve_ticket


def _resolve(ticket: OrderTicket, **overrides):
    kwargs = dict(
        reference_price=10.0,
        equity=100_000.0,
        buying_power=400_000.0,
        max_qty=10_000,
        max_notional=50_000.0,
    )
    kwargs.update(overrides)
    return resolve_ticket(ticket, **kwargs)


# --- shape, judged from the ticket alone ---------------------------------


def test_exactly_one_sizing_mode():
    """Both would be ambiguous; neither is unsized. Picking one silently
    would place a trade the user did not describe."""
    with pytest.raises(ValidationError):
        OrderTicket(symbol="AAPL", side="buy")
    with pytest.raises(ValidationError):
        OrderTicket(
            symbol="AAPL", side="buy", qty=10,
            risk=RiskSizing(stop_price=9.0, risk_amount=100.0),
        )


def test_exactly_one_risk_basis():
    with pytest.raises(ValidationError):
        RiskSizing(stop_price=9.0)
    with pytest.raises(ValidationError):
        RiskSizing(stop_price=9.0, risk_amount=100.0, risk_pct_of_equity=1.0)


def test_limit_orders_need_a_price_and_market_orders_must_not_have_one():
    with pytest.raises(ValidationError):
        OrderTicket(symbol="AAPL", side="buy", qty=10, order_type="limit")
    with pytest.raises(ValidationError):
        OrderTicket(symbol="AAPL", side="buy", qty=10, order_type="market", limit_price=10.0)


def test_order_class_is_derived_not_supplied():
    """Not accepting it from the client means a malformed leg combination is
    not expressible over the wire."""
    base = dict(symbol="AAPL", side="buy", qty=10)
    assert OrderTicket(**base).order_class == "simple"
    assert OrderTicket(**base, take_profit_price=12.0).order_class == "oto"
    assert OrderTicket(**base, take_profit_price=12.0, stop_loss_price=9.0).order_class == "bracket"


# --- resolution, which needs the account ---------------------------------


def test_manual_qty_resolves_with_notional():
    resolved = _resolve(OrderTicket(symbol="aapl", side="buy", qty=100))
    assert resolved.symbol == "AAPL"  # uppercased
    assert resolved.qty == 100
    assert resolved.notional == pytest.approx(1_000.0)
    assert resolved.order_class == "simple"


def test_risk_sizing_computes_quantity_from_the_stop():
    resolved = _resolve(
        OrderTicket(symbol="AAPL", side="buy", risk=RiskSizing(stop_price=9.50, risk_amount=200.0))
    )
    assert resolved.qty == 400
    assert resolved.risk_per_share == pytest.approx(0.50)


def test_risk_percent_uses_account_equity():
    resolved = _resolve(
        OrderTicket(
            symbol="AAPL", side="buy",
            risk=RiskSizing(stop_price=9.50, risk_pct_of_equity=1.0),
        )
    )
    # 1% of 100k = 1000, at 0.50/share
    assert resolved.qty == 2_000
    assert resolved.risk_pct_of_equity == 1.0


def test_sizing_from_a_stop_adopts_it_as_the_protective_leg():
    """Otherwise the order computes a quantity from a stop it never places --
    the risk would be theoretical."""
    resolved = _resolve(
        OrderTicket(symbol="AAPL", side="buy", risk=RiskSizing(stop_price=9.50, risk_amount=200.0))
    )
    assert resolved.stop_loss_price == 9.50
    assert resolved.order_class == "oto"


def test_an_explicit_stop_loss_wins_over_the_sizing_stop():
    resolved = _resolve(
        OrderTicket(
            symbol="AAPL", side="buy", stop_loss_price=9.0,
            risk=RiskSizing(stop_price=9.50, risk_amount=200.0),
        )
    )
    assert resolved.stop_loss_price == 9.0


@pytest.mark.parametrize(
    "kwargs,field",
    [
        (dict(take_profit_price=9.0), "take_profit_price"),
        (dict(stop_loss_price=11.0), "stop_loss_price"),
    ],
)
def test_bracket_legs_on_the_wrong_side_of_a_buy_are_refused(kwargs, field):
    """Alpaca rejects these too, but late and opaquely. Refusing here names
    the leg."""
    with pytest.raises(OrderRejected) as exc:
        _resolve(OrderTicket(symbol="AAPL", side="buy", qty=10, **kwargs))
    assert exc.value.field == field


def test_bracket_legs_are_mirrored_for_a_sell():
    _resolve(OrderTicket(symbol="AAPL", side="sell", qty=10, take_profit_price=9.0, stop_loss_price=11.0))
    with pytest.raises(OrderRejected):
        _resolve(OrderTicket(symbol="AAPL", side="sell", qty=10, take_profit_price=11.0))


def test_limit_price_is_the_entry_reference_not_the_last_trade():
    """The ceilings and the sizing must both use the price the order will
    actually work at."""
    resolved = _resolve(
        OrderTicket(symbol="AAPL", side="buy", qty=100, order_type="limit", limit_price=20.0),
        reference_price=10.0,
    )
    assert resolved.entry_reference == 20.0
    assert resolved.notional == pytest.approx(2_000.0)


def test_missing_price_is_refused_rather_than_guessed():
    with pytest.raises(OrderRejected) as exc:
        _resolve(OrderTicket(symbol="AAPL", side="buy", qty=10), reference_price=None)
    assert exc.value.field == "symbol"


def test_ceilings_and_buying_power_apply_to_resolved_quantity():
    with pytest.raises(OrderRejected, match="share ceiling"):
        _resolve(OrderTicket(symbol="AAPL", side="buy", qty=20_000))
    with pytest.raises(OrderRejected, match="order ceiling"):
        _resolve(OrderTicket(symbol="AAPL", side="buy", qty=9_000), max_notional=1_000.0)
    with pytest.raises(OrderRejected, match="buying power"):
        _resolve(OrderTicket(symbol="AAPL", side="buy", qty=1_000), buying_power=500.0)
