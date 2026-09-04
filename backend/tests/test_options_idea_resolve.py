"""The repair step between what a model proposes and what the options
stack will accept -- app.ai.options_resolve. Pure throughout: no Anthropic
SDK, no Alpaca, no network, module-local builders in the style of
test_spread_pricing.py."""

from datetime import date

import pytest

from app.ai.options_resolve import (
    ChainStrikes,
    IdeaUnresolvable,
    ProposedLeg,
    condense_chain,
    snap_legs,
    ticket_from_legs,
)
from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.models import LEGS_STRATEGIES

EXPIRY = date(2026, 9, 18)
FAR_EXPIRY = date(2026, 10, 16)
SPOT = 750.0


def _quote(kind, strike, *, bid=1.00, ask=1.10, oi=500, tradable=True, expiry=EXPIRY) -> LegQuote:
    mid = None if bid is None or ask is None else round((bid + ask) / 2, 4)
    return LegQuote(
        symbol=f"SPY{strike:g}{kind[0].upper()}",
        strike=strike,
        kind=kind,
        expiry=expiry,
        bid=bid,
        ask=ask,
        mid=mid,
        last=mid,
        bid_size=10,
        ask_size=10,
        delta=0.3,
        gamma=0.01,
        theta=-0.05,
        iv=0.22,
        open_interest=oi,
        tradable=tradable,
    )


def _chain(
    strikes=(735, 740, 745, 750, 755, 760, 765), *, expiry=EXPIRY, spot=SPOT, quotes=None
) -> Chain:
    """A well-behaved chain: both kinds quoted on every strike. `quotes`
    replaces individual (kind, strike) entries."""
    quotes = quotes or {}
    rows = []
    for strike in strikes:
        call = quotes.get(("call", strike), _quote("call", strike, expiry=expiry))
        put = quotes.get(("put", strike), _quote("put", strike, expiry=expiry))
        rows.append(StrikeRow(strike=float(strike), call=call, put=put))
    return Chain(
        underlying="SPY",
        expiry=expiry,
        spot=spot,
        feed="opra",
        as_of=None,  # type: ignore[arg-type]  -- unread by everything under test
        rows=rows,
    )


def _strikes(chain: Chain) -> ChainStrikes:
    return condense_chain(chain)[1]


def _both(*chains: Chain) -> dict:
    return {chain.expiry: _strikes(chain) for chain in chains}


# --- condense_chain -------------------------------------------------------


def test_condense_chain_drops_untradable_and_one_sided_quotes():
    chain = _chain(
        quotes={
            ("call", 745): _quote("call", 745, tradable=False),
            ("put", 750): _quote("put", 750, bid=None),
        }
    )
    rows, strikes = condense_chain(chain)

    assert 745.0 not in strikes.call
    assert 745.0 in strikes.put
    assert 750.0 not in strikes.put
    assert 750.0 in strikes.call
    assert next(r for r in rows if r["strike"] == 745.0)["call"] is None


def test_condense_chain_drops_a_wide_market_and_a_dead_strike():
    chain = _chain(
        quotes={
            # 0.50 wide on a 1.25 mid -- 40%, past both the fraction and
            # the absolute floor.
            ("call", 740): _quote("call", 740, bid=1.00, ask=1.50),
            ("put", 740): _quote("put", 740, oi=2),
        }
    )
    _, strikes = condense_chain(chain)

    assert 740.0 not in strikes.call
    assert 740.0 not in strikes.put


def test_condense_chain_keeps_a_wide_looking_market_on_a_cheap_contract():
    # 0.05/0.10 is 66% of mid and entirely normal -- the absolute floor is
    # what keeps it in.
    chain = _chain(quotes={("call", 765): _quote("call", 765, bid=0.05, ask=0.10)})
    _, strikes = condense_chain(chain)

    assert 765.0 in strikes.call


def test_condense_chain_drops_strikes_far_from_spot():
    chain = _chain(strikes=(500, 745, 750, 755, 1200))
    _, strikes = condense_chain(chain)

    assert 500.0 not in strikes.call
    assert 1200.0 not in strikes.call
    assert 750.0 in strikes.call


def test_condense_chain_keeps_the_strikes_nearest_spot_when_capped():
    chain = _chain(strikes=tuple(range(700, 801, 5)))
    rows, strikes = condense_chain(chain, max_strikes=4)

    assert [row["strike"] for row in rows] == [740.0, 745.0, 750.0, 755.0]
    assert strikes.call == (740.0, 745.0, 750.0, 755.0)


def test_condense_chain_rounds_the_numbers_it_shows():
    chain = _chain(quotes={("call", 750): _quote("call", 750, bid=1.234567, ask=1.345678)})
    rows, _ = condense_chain(chain)
    call = next(r for r in rows if r["strike"] == 750.0)["call"]

    assert call["bid"] == 1.23
    assert call["mid"] == 1.29
    assert call["iv"] == 0.22


def test_condense_chain_offers_only_strikes_it_also_allows_snapping_onto():
    chain = _chain(quotes={("call", 745): _quote("call", 745, tradable=False)})
    rows, strikes = condense_chain(chain)

    offered = {row["strike"] for row in rows if row["call"] is not None}
    assert offered == set(strikes.call)


# --- snap_legs: the strike-field shapes -----------------------------------


def test_snap_legs_moves_a_missing_strike_to_the_nearest_real_one():
    legs = [ProposedLeg(kind="call", strike=751.3, side="buy")]
    (leg,) = snap_legs(legs, _both(_chain()), "long_call", EXPIRY)

    assert leg.strike == 750.0
    assert (leg.kind, leg.side) == ("call", "buy")


def test_snap_legs_orders_a_bull_call_long_below_short():
    legs = [
        ProposedLeg(kind="call", strike=755.0, side="buy"),
        ProposedLeg(kind="call", strike=750.0, side="sell"),
    ]
    long, short = snap_legs(legs, _both(_chain()), "bull_call", EXPIRY)

    assert (long.side, long.strike) == ("buy", 750.0)
    assert (short.side, short.strike) == ("sell", 755.0)


def test_snap_legs_orders_a_bear_call_long_above_short():
    legs = [
        ProposedLeg(kind="call", strike=750.0, side="sell"),
        ProposedLeg(kind="call", strike=755.0, side="buy"),
    ]
    long, short = snap_legs(legs, _both(_chain()), "bear_call", EXPIRY)

    assert (long.side, long.strike) == ("buy", 755.0)
    assert (short.side, short.strike) == ("sell", 750.0)


def test_snap_legs_pushes_a_collapsed_vertical_one_strike_further_out():
    # Both legs round onto 750: the chain lists whole fives, the model
    # asked for a one-point-wide spread.
    legs = [
        ProposedLeg(kind="put", strike=750.0, side="buy"),
        ProposedLeg(kind="put", strike=749.0, side="sell"),
    ]
    long, short = snap_legs(legs, _both(_chain()), "bull_put", EXPIRY)

    assert long.strike < short.strike
    assert {long.strike, short.strike} == {750.0, 755.0}


def test_snap_legs_pulls_inward_when_the_top_of_the_chain_blocks_the_push():
    chain = _chain(strikes=(745, 750, 755, 760, 765))
    legs = [
        ProposedLeg(kind="call", strike=765.0, side="buy"),
        ProposedLeg(kind="call", strike=765.0, side="sell"),
    ]
    long, short = snap_legs(legs, _both(chain), "bull_call", EXPIRY)

    assert (long.strike, short.strike) == (760.0, 765.0)


def test_snap_legs_gives_up_when_the_chain_has_no_room():
    chain = _chain(strikes=(750,))
    legs = [
        ProposedLeg(kind="call", strike=750.0, side="buy"),
        ProposedLeg(kind="call", strike=755.0, side="sell"),
    ]
    with pytest.raises(IdeaUnresolvable):
        snap_legs(legs, _both(chain), "bull_call", EXPIRY)


def test_snap_legs_keeps_the_condor_strike_ordering():
    legs = [
        ProposedLeg(kind="put", strike=736.0, side="buy"),
        ProposedLeg(kind="put", strike=741.0, side="sell"),
        ProposedLeg(kind="call", strike=759.0, side="sell"),
        ProposedLeg(kind="call", strike=764.0, side="buy"),
    ]
    pl, ps, cs, cl = snap_legs(legs, _both(_chain()), "iron_condor", EXPIRY)

    assert pl.strike < ps.strike < cs.strike < cl.strike
    assert [leg.kind for leg in (pl, ps, cs, cl)] == ["put", "put", "call", "call"]
    assert [leg.side for leg in (pl, ps, cs, cl)] == ["buy", "sell", "sell", "buy"]


def test_snap_legs_separates_a_condor_whose_middle_legs_collapsed():
    legs = [
        ProposedLeg(kind="put", strike=744.0, side="buy"),
        ProposedLeg(kind="put", strike=746.0, side="sell"),
        ProposedLeg(kind="call", strike=746.0, side="sell"),
        ProposedLeg(kind="call", strike=754.0, side="buy"),
    ]
    pl, ps, cs, cl = snap_legs(legs, _both(_chain()), "iron_condor", EXPIRY)

    assert pl.strike < ps.strike < cs.strike < cl.strike


def test_snap_legs_ignores_the_models_sides_on_a_strike_field_shape():
    # Proposed the wrong way round for a bull call; the strategy decides.
    legs = [
        ProposedLeg(kind="put", strike=750.0, side="sell"),
        ProposedLeg(kind="put", strike=755.0, side="buy"),
    ]
    long, short = snap_legs(legs, _both(_chain()), "bull_call", EXPIRY)

    assert [long.kind, short.kind] == ["call", "call"]
    assert (long.side, short.side) == ("buy", "sell")
    assert long.strike < short.strike


# --- snap_legs: the leg-list shapes ---------------------------------------


def test_snap_legs_puts_a_straddle_on_one_strike_quoted_both_sides():
    legs = [
        ProposedLeg(kind="put", strike=749.0, side="buy"),
        ProposedLeg(kind="call", strike=752.0, side="buy"),
    ]
    put, call = snap_legs(legs, _both(_chain()), "long_straddle", EXPIRY)

    assert put.strike == call.strike == 750.0


def test_snap_legs_skips_a_straddle_strike_that_is_only_quoted_one_side():
    chain = _chain(quotes={("put", 750): _quote("put", 750, tradable=False)})
    legs = [
        ProposedLeg(kind="put", strike=750.0, side="buy"),
        ProposedLeg(kind="call", strike=750.0, side="buy"),
    ]
    put, call = snap_legs(legs, _both(chain), "long_straddle", EXPIRY)

    assert put.strike == call.strike
    assert put.strike != 750.0


def test_snap_legs_keeps_the_strangle_put_below_the_call():
    legs = [
        ProposedLeg(kind="put", strike=750.0, side="buy"),
        ProposedLeg(kind="call", strike=750.0, side="buy"),
    ]
    put, call = snap_legs(legs, _both(_chain()), "long_strangle", EXPIRY)

    assert put.strike < call.strike


def test_snap_legs_marks_the_butterfly_body_ratio_two():
    legs = [
        ProposedLeg(kind="call", strike=745.0, side="buy"),
        ProposedLeg(kind="call", strike=750.0, side="sell"),
        ProposedLeg(kind="call", strike=755.0, side="buy"),
    ]
    low, body, high = snap_legs(legs, _both(_chain()), "call_butterfly", EXPIRY)

    assert (low.ratio, body.ratio, high.ratio) == (1, 2, 1)
    assert (low.side, body.side, high.side) == ("buy", "sell", "buy")
    assert low.strike < body.strike < high.strike


def test_snap_legs_shares_the_iron_butterfly_body_across_both_kinds():
    legs = [
        ProposedLeg(kind="put", strike=740.0, side="buy"),
        ProposedLeg(kind="put", strike=749.0, side="sell"),
        ProposedLeg(kind="call", strike=751.0, side="sell"),
        ProposedLeg(kind="call", strike=760.0, side="buy"),
    ]
    put_long, put_short, call_short, call_long = snap_legs(legs, _both(_chain()), "iron_butterfly", EXPIRY)

    assert put_short.strike == call_short.strike
    assert put_long.strike < put_short.strike < call_long.strike


def test_snap_legs_puts_a_calendar_on_a_strike_quoted_in_both_expiries():
    near = _chain(strikes=(745, 750, 755))
    far = _chain(strikes=(750, 755, 760), expiry=FAR_EXPIRY)
    legs = [
        ProposedLeg(kind="call", strike=745.0, side="sell"),
        ProposedLeg(kind="call", strike=745.0, side="buy", expiry=FAR_EXPIRY),
    ]
    short, long = snap_legs(legs, _both(near, far), "calendar", EXPIRY)

    assert short.strike == long.strike == 750.0
    assert short.expiry == EXPIRY and long.expiry == FAR_EXPIRY


def test_snap_legs_keeps_a_diagonals_two_strikes_apart():
    near = _chain(strikes=(745, 750, 755))
    far = _chain(strikes=(745, 750, 755), expiry=FAR_EXPIRY)
    legs = [
        ProposedLeg(kind="call", strike=750.0, side="sell"),
        ProposedLeg(kind="call", strike=750.4, side="buy", expiry=FAR_EXPIRY),
    ]
    short, long = snap_legs(legs, _both(near, far), "diagonal", EXPIRY)

    assert short.strike != long.strike


def test_snap_legs_rejects_a_time_spread_whose_long_leg_is_not_further_out():
    chain = _chain()
    legs = [
        ProposedLeg(kind="call", strike=750.0, side="sell"),
        ProposedLeg(kind="call", strike=750.0, side="buy", expiry=EXPIRY),
    ]
    with pytest.raises(IdeaUnresolvable):
        snap_legs(legs, _both(chain), "calendar", EXPIRY)


def test_snap_legs_rejects_an_expiry_that_was_never_offered():
    legs = [ProposedLeg(kind="call", strike=750.0, side="buy")]
    with pytest.raises(IdeaUnresolvable):
        snap_legs(legs, _both(_chain()), "long_call", FAR_EXPIRY)


def test_snap_legs_rejects_the_wrong_number_of_legs():
    legs = [ProposedLeg(kind="call", strike=750.0, side="buy")]
    with pytest.raises(IdeaUnresolvable):
        snap_legs(legs, _both(_chain()), "iron_condor", EXPIRY)


# --- ticket_from_legs -----------------------------------------------------


def test_ticket_from_legs_uses_strike_fields_for_a_vertical():
    legs = snap_legs(
        [
            ProposedLeg(kind="call", strike=750.0, side="buy"),
            ProposedLeg(kind="call", strike=755.0, side="sell"),
        ],
        _both(_chain()),
        "bull_call",
        EXPIRY,
    )
    ticket = ticket_from_legs("SPY", "bull_call", EXPIRY, legs, 1)

    assert ticket.legs is None
    assert (ticket.long_strike, ticket.short_strike) == (750.0, 755.0)


def test_ticket_from_legs_uses_strike_fields_for_a_condor():
    legs = snap_legs(
        [
            ProposedLeg(kind="put", strike=735.0, side="buy"),
            ProposedLeg(kind="put", strike=740.0, side="sell"),
            ProposedLeg(kind="call", strike=760.0, side="sell"),
            ProposedLeg(kind="call", strike=765.0, side="buy"),
        ],
        _both(_chain()),
        "iron_condor",
        EXPIRY,
    )
    ticket = ticket_from_legs("SPY", "iron_condor", EXPIRY, legs, 1)

    assert ticket.legs is None
    assert ticket.put_long_strike < ticket.put_short_strike
    assert ticket.call_short_strike < ticket.call_long_strike


def test_ticket_from_legs_uses_the_legs_list_for_a_butterfly():
    legs = snap_legs(
        [
            ProposedLeg(kind="call", strike=745.0, side="buy"),
            ProposedLeg(kind="call", strike=750.0, side="sell"),
            ProposedLeg(kind="call", strike=755.0, side="buy"),
        ],
        _both(_chain()),
        "call_butterfly",
        EXPIRY,
    )
    ticket = ticket_from_legs("SPY", "call_butterfly", EXPIRY, legs, 1)

    assert ticket.long_strike is None
    assert [leg.ratio for leg in ticket.legs] == [1, 2, 1]


def test_ticket_from_legs_carries_the_limit_price_through():
    legs = snap_legs([ProposedLeg(kind="call", strike=750.0, side="buy")], _both(_chain()), "long_call", EXPIRY)
    ticket = ticket_from_legs("SPY", "long_call", EXPIRY, legs, 2, limit_price=1.25)

    assert (ticket.qty, ticket.limit_price) == (2, 1.25)


# --- the round trip that matters ------------------------------------------

# One plausible proposal per strategy. The point is not that these are good
# trades -- it is that whatever the model comes back with, snap_legs plus
# ticket_from_legs produce something SpreadTicket's own validation accepts.
# Anything this misses shows up in front of the user as a 422 instead.
_PROPOSALS: dict[str, list[ProposedLeg]] = {
    "long_call": [ProposedLeg(kind="call", strike=751.0, side="buy")],
    "long_put": [ProposedLeg(kind="put", strike=749.0, side="buy")],
    "bull_call": [
        ProposedLeg(kind="call", strike=749.0, side="buy"),
        ProposedLeg(kind="call", strike=754.0, side="sell"),
    ],
    "bear_put": [
        ProposedLeg(kind="put", strike=754.0, side="buy"),
        ProposedLeg(kind="put", strike=749.0, side="sell"),
    ],
    "bull_put": [
        ProposedLeg(kind="put", strike=741.0, side="buy"),
        ProposedLeg(kind="put", strike=746.0, side="sell"),
    ],
    "bear_call": [
        ProposedLeg(kind="call", strike=761.0, side="buy"),
        ProposedLeg(kind="call", strike=756.0, side="sell"),
    ],
    "iron_condor": [
        ProposedLeg(kind="put", strike=736.0, side="buy"),
        ProposedLeg(kind="put", strike=741.0, side="sell"),
        ProposedLeg(kind="call", strike=759.0, side="sell"),
        ProposedLeg(kind="call", strike=764.0, side="buy"),
    ],
    "long_straddle": [
        ProposedLeg(kind="put", strike=750.4, side="buy"),
        ProposedLeg(kind="call", strike=749.6, side="buy"),
    ],
    "long_strangle": [
        ProposedLeg(kind="put", strike=744.0, side="buy"),
        ProposedLeg(kind="call", strike=756.0, side="buy"),
    ],
    "call_butterfly": [
        ProposedLeg(kind="call", strike=744.0, side="buy"),
        ProposedLeg(kind="call", strike=751.0, side="sell"),
        ProposedLeg(kind="call", strike=756.0, side="buy"),
    ],
    "put_butterfly": [
        ProposedLeg(kind="put", strike=744.0, side="buy"),
        ProposedLeg(kind="put", strike=751.0, side="sell"),
        ProposedLeg(kind="put", strike=756.0, side="buy"),
    ],
    "iron_butterfly": [
        ProposedLeg(kind="put", strike=741.0, side="buy"),
        ProposedLeg(kind="put", strike=749.0, side="sell"),
        ProposedLeg(kind="call", strike=751.0, side="sell"),
        ProposedLeg(kind="call", strike=759.0, side="buy"),
    ],
    "calendar": [
        ProposedLeg(kind="call", strike=751.0, side="sell"),
        ProposedLeg(kind="call", strike=751.0, side="buy", expiry=FAR_EXPIRY),
    ],
    "diagonal": [
        ProposedLeg(kind="call", strike=756.0, side="sell"),
        ProposedLeg(kind="call", strike=749.0, side="buy", expiry=FAR_EXPIRY),
    ],
    "covered_call": [ProposedLeg(kind="call", strike=756.0, side="sell")],
    "cash_secured_put": [ProposedLeg(kind="put", strike=744.0, side="sell")],
}


@pytest.mark.parametrize("strategy", sorted(_PROPOSALS))
def test_every_strategy_round_trips_into_a_ticket_the_stack_accepts(strategy):
    chains = _both(_chain(), _chain(expiry=FAR_EXPIRY))
    legs = snap_legs(_PROPOSALS[strategy], chains, strategy, EXPIRY)
    ticket = ticket_from_legs("SPY", strategy, EXPIRY, legs, 1)

    assert ticket.strategy == strategy
    assert (ticket.legs is not None) == (strategy in LEGS_STRATEGIES)
    # leg_specs_full() is what pricing and the order builder read; if the
    # canonical view comes back empty the ticket is shaped wrong.
    assert ticket.leg_specs_full()


def test_the_proposal_table_covers_every_strategy():
    from typing import get_args

    from app.options.models import Strategy

    assert set(_PROPOSALS) == set(get_args(Strategy))
