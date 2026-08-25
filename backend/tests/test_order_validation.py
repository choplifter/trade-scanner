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
        max_notional_pct=None,
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


def test_stop_entries_need_a_trigger_and_only_stop_limits_take_both_prices():
    with pytest.raises(ValidationError):
        OrderTicket(symbol="AAPL", side="buy", qty=10, order_type="stop")
    with pytest.raises(ValidationError):
        OrderTicket(symbol="AAPL", side="buy", qty=10, order_type="stop", stop_price=11.0, limit_price=11.2)
    with pytest.raises(ValidationError):
        OrderTicket(symbol="AAPL", side="buy", qty=10, order_type="stop_limit", stop_price=11.0)
    with pytest.raises(ValidationError):
        OrderTicket(symbol="AAPL", side="buy", qty=10, order_type="limit", limit_price=11.0, stop_price=11.0)
    assert OrderTicket(symbol="AAPL", side="buy", qty=10, order_type="stop", stop_price=11.0).stop_price == 11.0
    ticket = OrderTicket(
        symbol="AAPL", side="buy", qty=10, order_type="stop_limit", stop_price=11.0, limit_price=11.2
    )
    assert (ticket.stop_price, ticket.limit_price) == (11.0, 11.2)


def test_a_stop_limits_limit_sits_on_the_far_side_of_its_trigger():
    """Buy: limit at or above the trigger. Sell: at or below. Inverted, the
    order could only fill on a pullback after triggering."""
    with pytest.raises(ValidationError):
        OrderTicket(symbol="AAPL", side="buy", qty=10, order_type="stop_limit", stop_price=11.0, limit_price=10.9)
    with pytest.raises(ValidationError):
        OrderTicket(symbol="AAPL", side="sell", qty=10, order_type="stop_limit", stop_price=9.0, limit_price=9.1)
    OrderTicket(symbol="AAPL", side="sell", qty=10, order_type="stop_limit", stop_price=9.0, limit_price=8.9)


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


# --- the live incident: limits above the market, stops above the fill -----
#
# Six DAIC buy limits at 4.50-5.00 against a 4.2 market, every one filled
# on arrival; two carried a 4.50 stop-loss that was above the 4.2 fill and
# fired on the next tick. What the user wanted was a breakout entry.


def test_a_marketable_limit_is_allowed_but_warned():
    """A buy limit above the market is a capped market order, which is a
    legitimate thing to want -- so not a refusal. But it is not the resting
    order it looks like, and the preview must say so."""
    resolved = _resolve(
        OrderTicket(symbol="DAIC", side="buy", qty=100, order_type="limit", limit_price=5.0),
        reference_price=4.2,
    )
    assert len(resolved.warnings) == 1
    assert "fills immediately" in resolved.warnings[0]
    assert "Stop order" in resolved.warnings[0]

    resting = _resolve(
        OrderTicket(symbol="DAIC", side="buy", qty=100, order_type="limit", limit_price=4.0),
        reference_price=4.2,
    )
    assert resting.warnings == []


def test_a_sell_limit_below_the_market_is_warned_too():
    resolved = _resolve(
        OrderTicket(symbol="DAIC", side="sell", qty=100, order_type="limit", limit_price=4.0),
        reference_price=4.2,
    )
    assert len(resolved.warnings) == 1


def test_market_and_stop_entries_carry_no_marketability_warning():
    assert _resolve(OrderTicket(symbol="DAIC", side="buy", qty=100), reference_price=4.2).warnings == []
    assert _resolve(
        OrderTicket(symbol="DAIC", side="buy", qty=100, order_type="stop", stop_price=5.0),
        reference_price=4.2,
    ).warnings == []


def test_a_stop_loss_above_where_a_marketable_limit_will_fill_is_refused():
    """The 4.50 stop under a 5.00 limit on a 4.2 market. Checked against
    the limit it passed; checked against the fill it does not."""
    with pytest.raises(OrderRejected) as exc:
        _resolve(
            OrderTicket(
                symbol="DAIC", side="buy", qty=100, order_type="limit", limit_price=5.0,
                stop_loss_price=4.5,
            ),
            reference_price=4.2,
        )
    assert exc.value.field == "stop_loss_price"
    assert "4.2000" in exc.value.message


def test_the_sizing_stop_is_held_to_the_same_rule():
    """The 13:54 order: risk-sized from a 4.50 stop under a 5.00 limit. The
    sizing stop becomes the stop-loss leg, so it cannot be exempt."""
    with pytest.raises(OrderRejected) as exc:
        _resolve(
            OrderTicket(
                symbol="DAIC", side="buy", order_type="limit", limit_price=5.0,
                risk=RiskSizing(stop_price=4.5, risk_amount=200.0),
            ),
            reference_price=4.2,
        )
    assert exc.value.field == "stop_loss_price"


def test_a_take_profit_between_the_fill_and_a_marketable_limit_is_fine():
    """Fill will be ~4.2, so a 4.8 target is above it -- even though it is
    below the 5.00 written on the order."""
    resolved = _resolve(
        OrderTicket(
            symbol="DAIC", side="buy", qty=100, order_type="limit", limit_price=5.0,
            take_profit_price=4.8, stop_loss_price=4.0,
        ),
        reference_price=4.2,
    )
    assert resolved.order_class == "bracket"


def test_a_resting_limit_still_checks_legs_against_the_limit():
    """Below the market, the limit IS where it fills."""
    with pytest.raises(OrderRejected) as exc:
        _resolve(
            OrderTicket(
                symbol="DAIC", side="buy", qty=100, order_type="limit", limit_price=4.0,
                stop_loss_price=4.1,
            ),
            reference_price=4.2,
        )
    assert exc.value.field == "stop_loss_price"


def test_a_buy_stop_below_the_market_is_refused():
    """Not a trigger -- it would fire on arrival, the very thing the user
    reached for a stop order to avoid."""
    with pytest.raises(OrderRejected) as exc:
        _resolve(
            OrderTicket(symbol="DAIC", side="buy", qty=100, order_type="stop", stop_price=4.0),
            reference_price=4.2,
        )
    assert exc.value.field == "stop_price"
    with pytest.raises(OrderRejected):
        _resolve(
            OrderTicket(symbol="DAIC", side="sell", qty=100, order_type="stop", stop_price=4.5),
            reference_price=4.2,
        )


def test_a_stop_entry_is_sized_at_its_trigger_and_protected_below_it():
    """The breakout the user meant: in at 5.00 on the way up, stop 4.50.
    The stop-loss sits between the market and the trigger, which is fine --
    it only exists once the entry has filled at 5.00."""
    resolved = _resolve(
        OrderTicket(
            symbol="DAIC", side="buy", order_type="stop", stop_price=5.0,
            risk=RiskSizing(stop_price=4.5, risk_amount=200.0),
        ),
        reference_price=4.2,
    )
    assert resolved.entry_reference == 5.0
    assert resolved.stop_price == 5.0
    assert resolved.stop_loss_price == 4.5
    assert resolved.qty == 400
    assert resolved.order_class == "oto"


def test_a_stop_limit_is_sized_at_its_limit_the_worst_case():
    resolved = _resolve(
        OrderTicket(
            symbol="DAIC", side="buy", qty=100, order_type="stop_limit", stop_price=5.0, limit_price=5.1,
        ),
        reference_price=4.2,
    )
    assert resolved.entry_reference == 5.1
    assert resolved.notional == pytest.approx(510.0)


def test_without_a_reference_price_a_stop_entry_still_resolves():
    """The market checks need the last trade; without one they are skipped
    rather than guessed -- the broker applies its own."""
    resolved = _resolve(
        OrderTicket(symbol="DAIC", side="buy", qty=100, order_type="stop", stop_price=5.0),
        reference_price=None,
    )
    assert resolved.entry_reference == 5.0
    assert resolved.warnings == []


def test_ceilings_and_buying_power_apply_to_resolved_quantity():
    with pytest.raises(OrderRejected, match="share ceiling"):
        _resolve(OrderTicket(symbol="AAPL", side="buy", qty=20_000))
    with pytest.raises(OrderRejected, match="order ceiling"):
        _resolve(OrderTicket(symbol="AAPL", side="buy", qty=9_000), max_notional=1_000.0)
    with pytest.raises(OrderRejected, match="buying power"):
        _resolve(OrderTicket(symbol="AAPL", side="buy", qty=1_000), buying_power=500.0)


def test_notional_ceiling_scales_with_equity():
    """A fixed ceiling cannot work at every account size. Sizing from a stop
    makes notional = risk x (entry / stop-distance), so a 1% risk on a large
    account routinely builds a position many times the risk -- the first
    version of this shipped a flat 5,000 alongside a 1% default and blocked
    every realistic trade while looking like a safety feature."""
    ticket = OrderTicket(symbol="AAPL", side="buy", qty=1_000)  # 10,000 notional at 10.0

    # 25% of a 100k account is 25,000 -- allowed.
    _resolve(ticket, equity=100_000.0, max_notional=1e9, max_notional_pct=25.0)

    # The same order against a 20k account is half the account -- refused.
    with pytest.raises(OrderRejected, match="order ceiling"):
        _resolve(ticket, equity=20_000.0, max_notional=1e9, max_notional_pct=25.0)


def test_the_absolute_backstop_still_applies_independently():
    """It exists so the percentage does not have to be right on its own --
    if equity were ever misreported high, this still bounds the order."""
    with pytest.raises(OrderRejected, match="order ceiling"):
        _resolve(
            OrderTicket(symbol="AAPL", side="buy", qty=1_000),
            equity=10_000_000.0,  # absurd equity would permit anything by percentage
            max_notional=5_000.0,
            max_notional_pct=25.0,
        )


def test_the_ceiling_message_says_what_to_change():
    """The number alone does not explain why a small risk produced a large
    position."""
    with pytest.raises(OrderRejected) as exc:
        _resolve(OrderTicket(symbol="AAPL", side="buy", qty=1_000), max_notional=500.0)
    assert "widen" in exc.value.message and "risk %" in exc.value.message


# --- how long the order lives --------------------------------------------
#
# The case these exist for: four positions were found held overnight with
# every take-profit `expired` and every stop-loss `canceled` at the previous
# close. The entries were day orders, and Alpaca applies one time_in_force to
# a whole bracket -- so the protection died with the session while the
# position it protected did not.


def _ticket(**kwargs):
    return OrderTicket(symbol="AAA", side="buy", **kwargs)


def test_a_bare_ticket_is_a_day_order():
    """Nothing to outlive the session, so nothing to keep working."""
    assert _ticket(qty=10).time_in_force == "day"


def test_a_take_profit_makes_it_good_till_cancelled():
    assert _ticket(qty=10, take_profit_price=12.0).time_in_force == "gtc"


def test_a_stop_loss_makes_it_good_till_cancelled():
    assert _ticket(qty=10, stop_loss_price=8.0).time_in_force == "gtc"


def test_risk_sizing_counts_as_protection_even_with_no_explicit_stop():
    """The subtle one. A risk-sized ticket names no stop_loss_price, but
    resolve_ticket adopts the sizing stop as the stop-loss leg -- so it does
    place protection, and that protection has to survive the close."""
    ticket = _ticket(risk=RiskSizing(stop_price=8.0, risk_pct_of_equity=1.0))

    assert ticket.has_protective_exit
    assert ticket.time_in_force == "gtc"
    assert _resolve(ticket).stop_loss_price == 8.0


@pytest.mark.parametrize("chosen", ["day", "gtc"])
def test_an_explicit_choice_always_wins(chosen):
    """The default is a default, not a policy -- someone who deliberately
    wants a day bracket gets one."""
    ticket = _ticket(qty=10, take_profit_price=12.0, stop_loss_price=8.0, time_in_force=chosen)

    assert ticket.time_in_force == chosen


def test_the_choice_survives_into_the_resolved_order():
    """What the ticket is submitted as, and what the client echoes back on
    confirm -- submitting without it is how a bracket silently becomes a day
    order nobody chose."""
    resolved = _resolve(_ticket(qty=10, take_profit_price=12.0))

    assert resolved.time_in_force == "gtc"
    assert resolved.order_class == "oto"


def test_only_the_two_values_a_bracket_accepts_are_allowed():
    """Alpaca takes day or gtc on a bracket and nothing else, and every
    ticket here can become one."""
    with pytest.raises(ValidationError):
        _ticket(qty=10, time_in_force="ioc")
