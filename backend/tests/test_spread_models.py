from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.models import (
    CloseLeg,
    CloseSpreadRequest,
    SpreadTicket,
    TriggerCreate,
    closing_legs,
    resolve_legs,
)
from app.trading.errors import OrderRejected

EXPIRY = date(2026, 9, 18)


def _ticket(**overrides) -> SpreadTicket:
    base = dict(underlying="SPY", strategy="bull_put", expiry=EXPIRY, qty=1, long_strike=740, short_strike=745)
    base.update(overrides)
    return SpreadTicket(**base)


def _quote(kind, strike, *, tradable=True, bid=1.0, ask=1.2, delta=-0.3) -> LegQuote:
    return LegQuote(
        symbol=f"SPY260918{'C' if kind == 'call' else 'P'}{round(strike * 1000):08d}",
        strike=strike,
        kind=kind,
        expiry=EXPIRY,
        bid=bid,
        ask=ask,
        mid=round((bid + ask) / 2, 4),
        last=None,
        bid_size=1,
        ask_size=1,
        delta=delta,
        gamma=None,
        theta=None,
        iv=0.2,
        open_interest=10,
        tradable=tradable,
    )


def _chain(strikes=(735, 740, 745, 750, 755), **quote_overrides) -> Chain:
    rows = [
        StrikeRow(strike=s, call=_quote("call", s, **quote_overrides), put=_quote("put", s, **quote_overrides))
        for s in strikes
    ]
    return Chain(underlying="SPY", expiry=EXPIRY, spot=748.0, feed="opra", as_of=datetime.now(timezone.utc), rows=rows)


# --- ticket shape ------------------------------------------------------------


def test_vertical_direction_and_canonical_strikes():
    assert _ticket().direction == "credit"
    assert _ticket().strikes == (740, 745)
    assert _ticket(strategy="bull_call", long_strike=745, short_strike=750).direction == "debit"


@pytest.mark.parametrize(
    "strategy,long,short",
    [("bull_call", 750, 745), ("bull_put", 750, 745), ("bear_put", 745, 750), ("bear_call", 745, 750)],
)
def test_vertical_long_short_ordering_is_enforced(strategy, long, short):
    with pytest.raises(ValidationError):
        _ticket(strategy=strategy, long_strike=long, short_strike=short)


def test_vertical_requires_both_strikes_and_no_condor_fields():
    with pytest.raises(ValidationError):
        _ticket(short_strike=None)
    with pytest.raises(ValidationError):
        _ticket(put_long_strike=700)
    with pytest.raises(ValidationError):
        _ticket(long_strike=745, short_strike=745)


def test_iron_condor_shape():
    condor = SpreadTicket(
        underlying="SPY",
        strategy="iron_condor",
        expiry=EXPIRY,
        qty=2,
        put_long_strike=735,
        put_short_strike=740,
        call_short_strike=755,
        call_long_strike=760,
    )
    assert condor.direction == "credit"
    assert condor.strikes == (735, 740, 755, 760)
    assert [s for _, _, s in condor.leg_specs()] == ["buy", "sell", "sell", "buy"]
    with pytest.raises(ValidationError):
        SpreadTicket(
            underlying="SPY",
            strategy="iron_condor",
            expiry=EXPIRY,
            qty=1,
            put_long_strike=740,
            put_short_strike=735,
            call_short_strike=755,
            call_long_strike=760,
        )
    with pytest.raises(ValidationError):
        SpreadTicket(underlying="SPY", strategy="iron_condor", expiry=EXPIRY, qty=1, long_strike=1, short_strike=2)


def test_leg_specs_for_each_vertical():
    assert _ticket(strategy="bull_call", long_strike=745, short_strike=750).leg_specs() == [
        ("call", 745, "buy"),
        ("call", 750, "sell"),
    ]
    assert _ticket(strategy="bear_put", long_strike=750, short_strike=745).leg_specs() == [
        ("put", 750, "buy"),
        ("put", 745, "sell"),
    ]
    assert _ticket(strategy="bear_call", long_strike=755, short_strike=750).leg_specs() == [
        ("call", 755, "buy"),
        ("call", 750, "sell"),
    ]


# --- resolve_legs -------------------------------------------------------------


def test_resolve_legs_takes_symbols_and_quotes_from_the_chain():
    legs = resolve_legs(_ticket(), _chain())
    assert [leg.symbol for leg in legs] == ["SPY260918P00740000", "SPY260918P00745000"]
    assert [leg.position_intent for leg in legs] == ["buy_to_open", "sell_to_open"]
    assert legs[0].mid == 1.1 and legs[0].delta == -0.3


def test_resolve_legs_refuses_a_missing_strike():
    with pytest.raises(OrderRejected) as exc:
        resolve_legs(_ticket(long_strike=700, short_strike=745), _chain())
    assert exc.value.field == "strikes"


def test_resolve_legs_refuses_a_non_tradable_contract():
    with pytest.raises(OrderRejected):
        resolve_legs(_ticket(), _chain(tradable=False))


def test_resolve_legs_refuses_a_chain_for_another_expiry():
    chain = _chain()
    chain.expiry = date(2026, 9, 25)
    with pytest.raises(OrderRejected) as exc:
        resolve_legs(_ticket(), chain)
    assert exc.value.field == "expiry"


# --- closing ------------------------------------------------------------------


def test_closing_legs_reverse_what_is_held():
    held = [CloseLeg(symbol="SPY260918P00740000", qty=2), CloseLeg(symbol="SPY260918P00745000", qty=-2)]
    legs = closing_legs(held)
    assert [(leg.side, leg.position_intent) for leg in legs] == [
        ("sell", "sell_to_close"),
        ("buy", "buy_to_close"),
    ]
    assert legs[0].strike == 740 and legs[0].kind == "put" and legs[0].expiry == EXPIRY


def test_close_request_bounds_qty_by_what_is_held():
    legs = [CloseLeg(symbol="SPY260918P00740000", qty=2), CloseLeg(symbol="SPY260918P00745000", qty=-2)]
    CloseSpreadRequest(legs=legs, qty=2)
    with pytest.raises(ValidationError):
        CloseSpreadRequest(legs=legs, qty=3)
    with pytest.raises(ValidationError):
        CloseSpreadRequest(legs=[legs[0], legs[0]], qty=1)
    with pytest.raises(ValidationError):
        CloseLeg(symbol="SPY", qty=1)


def test_trigger_needs_a_bound_and_sane_ordering():
    legs = [CloseLeg(symbol="SPY260918P00740000", qty=1), CloseLeg(symbol="SPY260918P00745000", qty=-1)]
    TriggerCreate(underlying="SPY", expiry=EXPIRY, legs=legs, qty=1, close_below=740)
    TriggerCreate(underlying="SPY", expiry=EXPIRY, legs=legs, qty=1, close_below=740, close_above=760)
    with pytest.raises(ValidationError):
        TriggerCreate(underlying="SPY", expiry=EXPIRY, legs=legs, qty=1)
    with pytest.raises(ValidationError):
        TriggerCreate(underlying="SPY", expiry=EXPIRY, legs=legs, qty=1, close_below=760, close_above=740)


# --- single leg ----------------------------------------------------------------


def test_single_leg_uses_long_strike_only():
    t = _ticket(strategy="long_call", long_strike=750, short_strike=None)
    assert t.direction == "debit" and t.strikes == (750,)
    assert t.leg_specs() == [("call", 750, "buy")]
    assert _ticket(strategy="long_put", long_strike=740, short_strike=None).leg_specs() == [("put", 740, "buy")]
    with pytest.raises(ValidationError, match="long_strike is required"):
        _ticket(strategy="long_put", long_strike=None, short_strike=None)
    with pytest.raises(ValidationError, match="only long_strike"):
        _ticket(strategy="long_call", long_strike=750, short_strike=755)


def test_resolve_single_leg_from_the_chain():
    legs = resolve_legs(_ticket(strategy="long_call", long_strike=750, short_strike=None), _chain())
    assert len(legs) == 1
    assert legs[0].symbol == "SPY260918C00750000" and legs[0].position_intent == "buy_to_open"
