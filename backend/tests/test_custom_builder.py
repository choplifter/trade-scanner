"""The freely built position: which legs count as uncovered, what level
that needs, what it ties up, and the two probabilities."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.options.custom import chance_of_touch, custom_risk, naked_margin, nearest_breakeven
from app.options.models import SpreadLeg, SpreadTicket, TicketLeg, level_for_legs, naked_shorts

EXPIRY = date(2026, 10, 16)
LATER = date(2026, 11, 20)


def _ticket(*legs: TicketLeg, qty: int = 1) -> SpreadTicket:
    return SpreadTicket(underlying="SPY", strategy="custom", expiry=EXPIRY, qty=qty, legs=list(legs))


def _leg(kind: str, strike: float, side: str, ratio: int = 1, expiry: date = EXPIRY, mid: float | None = None) -> SpreadLeg:
    return SpreadLeg(
        symbol=f"X{strike}", kind=kind, strike=strike, expiry=expiry, side=side,
        position_intent="buy_to_open" if side == "buy" else "sell_to_open", ratio_qty=ratio, mid=mid,
    )


def test_a_long_above_a_short_call_covers_it():
    legs = _ticket(
        TicketLeg(kind="call", strike=770, side="sell"),
        TicketLeg(kind="call", strike=780, side="buy"),
    ).leg_specs_full()
    assert naked_shorts(legs) == []
    assert level_for_legs("custom", legs) == 3


def test_a_lone_short_is_naked_and_is_not_a_level_question():
    """Alpaca has three levels and no fourth, and takes no uncovered short
    at any of them -- so the level stays 3 and the package is refused for
    what it is (see OptionsService.submit)."""
    legs = _ticket(TicketLeg(kind="call", strike=770, side="sell")).leg_specs_full()
    assert [leg.strike for leg in naked_shorts(legs)] == [770]
    assert level_for_legs("custom", legs) == 3


def test_a_long_of_the_same_kind_covers_whichever_side_it_sits_on():
    """A 760 long call caps a 770 short just as a 780 one does -- above
    the short the long is already gaining, below it the loss is capped at
    the distance. The payoff curve says how big the cap is; this only asks
    whether there is one."""
    above = _ticket(
        TicketLeg(kind="call", strike=770, side="sell"),
        TicketLeg(kind="call", strike=780, side="buy"),
    ).leg_specs_full()
    below = _ticket(
        TicketLeg(kind="call", strike=770, side="sell"),
        TicketLeg(kind="call", strike=760, side="buy"),
    ).leg_specs_full()
    assert naked_shorts(above) == []
    assert naked_shorts(below) == []

    puts = _ticket(
        TicketLeg(kind="put", strike=740, side="sell"),
        TicketLeg(kind="put", strike=750, side="buy"),
    ).leg_specs_full()
    assert naked_shorts(puts) == []


def test_a_long_of_the_other_kind_covers_nothing():
    """A long put does nothing for a short call: the call's loss is above
    the market, the put pays below it."""
    legs = _ticket(
        TicketLeg(kind="call", strike=770, side="sell"),
        TicketLeg(kind="put", strike=750, side="buy"),
    ).leg_specs_full()
    assert [leg.strike for leg in naked_shorts(legs)] == [770]


def test_ratios_leave_the_extra_short_bare():
    """Two shorts against one long: one of them has nothing behind it."""
    legs = _ticket(
        TicketLeg(kind="call", strike=770, side="sell", ratio=2),
        TicketLeg(kind="call", strike=780, side="buy"),
    ).leg_specs_full()
    assert [leg.strike for leg in naked_shorts(legs)] == [770]
    assert level_for_legs("custom", legs) == 3


def test_a_long_that_expires_first_does_not_cover():
    legs = _ticket(
        TicketLeg(kind="call", strike=770, side="sell", expiry=LATER),
        TicketLeg(kind="call", strike=780, side="buy", expiry=EXPIRY),
    ).leg_specs_full()
    assert [leg.strike for leg in naked_shorts(legs)] == [770]
    # The other way round -- the long outliving the short -- is a diagonal
    # and covered.
    covered = _ticket(
        TicketLeg(kind="call", strike=770, side="sell", expiry=EXPIRY),
        TicketLeg(kind="call", strike=780, side="buy", expiry=LATER),
    ).leg_specs_full()
    assert naked_shorts(covered) == []


def test_all_long_legs_need_only_level_two():
    legs = _ticket(
        TicketLeg(kind="call", strike=770, side="buy"),
        TicketLeg(kind="put", strike=750, side="buy"),
    ).leg_specs_full()
    assert level_for_legs("custom", legs) == 2


def test_the_same_contract_twice_is_refused():
    with pytest.raises(ValidationError):
        _ticket(
            TicketLeg(kind="call", strike=770, side="buy"),
            TicketLeg(kind="call", strike=770, side="sell"),
        )


def test_defined_risk_ties_up_what_it_can_lose():
    legs = [_leg("call", 770, "sell", mid=3.0), _leg("call", 780, "buy", mid=1.0)]
    risk = custom_risk(legs, [], qty=2, spot=760.0, max_profit=400.0, max_loss=-1600.0, breakevens=[772.0])
    assert risk.naked is False
    assert risk.collateral == 1600.0
    assert risk.width == 10.0


def test_an_uncovered_short_falls_back_to_the_brokers_margin():
    """No ceiling to the loss, so the collateral is Reg-T's requirement --
    a fifth of the underlying less the out-of-the-money amount, plus the
    premium, and never below a tenth."""
    legs = [_leg("call", 770, "sell", mid=2.0)]
    risk = custom_risk(legs, legs, qty=1, spot=760.0, max_profit=200.0, max_loss=None, breakevens=[772.0])
    assert risk.naked is True
    # 0.20 * 760 - 10 = 142, + 2 premium, x 100.
    assert risk.collateral == naked_margin("call", 770, 760, 2.0) == 14_400.0
    # Two contracts tie up twice as much.
    assert custom_risk(legs, legs, qty=2, spot=760.0, max_profit=400.0, max_loss=None, breakevens=[772.0]).collateral == 28_800.0


def test_the_margin_floor_applies_far_out_of_the_money():
    """A 900 call on a 760 underlying: a fifth less the distance is
    negative, so the tenth is what stands."""
    assert naked_margin("call", 900, 760, 0.05) == pytest.approx(0.10 * 760 * 100 + 5, abs=0.5)


def test_the_chance_of_touch_is_about_twice_the_chance_of_finishing_beyond():
    """The reflection principle: a barrier is reached roughly twice as
    often as it is finished beyond."""
    touch = chance_of_touch(100.0, 105.0, 0.20, 30 / 365)
    assert touch is not None and 0.35 < touch < 0.45
    # Standing on the barrier means it has been touched.
    assert chance_of_touch(100.0, 100.0, 0.20, 30 / 365) == 1.0
    # Further away is less likely, and no volatility or no time is no answer.
    assert chance_of_touch(100.0, 130.0, 0.20, 30 / 365) < touch
    assert chance_of_touch(100.0, 105.0, 0.0, 30 / 365) is None
    assert chance_of_touch(100.0, 105.0, 0.20, 0.0) is None


def test_the_nearest_breakeven_is_the_one_reported():
    assert nearest_breakeven([740.0, 772.0], 760.0) == 772.0
    assert nearest_breakeven([], 760.0) is None
