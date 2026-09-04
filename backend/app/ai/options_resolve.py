"""A model's proposed options structure, snapped onto strikes that exist.

An LLM asked for a concrete structure will sometimes name a strike that is
not listed, or one whose contract nothing quotes. Asking it again is the
obvious reflex and the wrong one: it is slower, it costs another call, and
it is no more likely to land on a real strike than the first attempt. So a
proposal is repaired here instead, deterministically -- every strike moves
to the nearest one that survived the pre-filter the model was shown, and
the per-strategy ordering SpreadTicket enforces is restored afterwards.

The pre-filter and the strike sets used for snapping come out of the same
call (condense_chain), so the two cannot drift: the model is never shown a
strike the resolver would refuse to snap to, and never snapped onto one it
was not shown.

Pure on purpose. Chain is a plain dataclass and SpreadTicket a pydantic
model, so nothing here needs the Anthropic SDK or an Alpaca client -- the
part of this feature most likely to fail silently is the part that is
fully unit-testable, in the same spirit as app.options.pricing and
app.market_data.gex_plan.

What the model does *not* get to decide: for the strategies SpreadTicket
describes with strike fields (the verticals, the condor, a long call/put)
kind and side follow from the strategy itself -- see leg_specs() -- so
only the strikes are read off the proposal. A "bull call" whose legs came
back as sell-low/buy-high is a bull call with two strikes, not a silently
reinterpreted bear call.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal, Sequence

from pydantic import BaseModel, Field

from app.options.chain import Chain, LegQuote
from app.options.models import (
    LEGS_STRATEGIES,
    TIME_STRATEGIES,
    SpreadTicket,
    Strategy,
    TicketLeg,
)
from app.options.occ import Kind

# The market on a quote may not be wider than the ticket's own "wide
# market" warning already flags (app.options.service._WIDE_MARKET_FRACTION),
# so a suggestion never leads with a fill the ticket would immediately
# complain about.
MAX_SPREAD_FRACTION = 0.25
# Under this the fraction alone says nothing: a 0.05 / 0.10 market is 66%
# wide by mid and entirely normal on a cheap contract.
MIN_ABS_SPREAD = 0.05
# A strike nobody holds. The greeks are usually still there; the fill is
# not.
MIN_OPEN_INTEREST = 10
# Further out than this contributes nothing to the structures the ticket
# can build and only makes the payload bigger.
STRIKE_PCT_RANGE = 0.12
MAX_STRIKES_PER_EXPIRY = 24

# Kind and side per strategy for the strike-field shapes -- mirrors
# SpreadTicket.leg_specs(), which is what the ticket itself will derive
# once these strikes are on it.
_VERTICAL_KIND: dict[str, Kind] = {
    "bull_call": "call",
    "bear_put": "put",
    "bull_put": "put",
    "bear_call": "call",
}
_LONG_BELOW_SHORT: frozenset[str] = frozenset({"bull_call", "bull_put"})
_SINGLE_KIND: dict[str, Kind] = {"long_call": "call", "long_put": "put"}


class IdeaUnresolvable(Exception):
    """The proposal cannot be put onto real strikes at all -- the expiry is
    not one that was offered, the chain has no room to separate two legs
    that collapsed onto one strike, and so on. The caller drops the idea
    and reports the reason rather than silently showing a shorter list."""


class ProposedLeg(BaseModel):
    """One leg as the model proposes it -- before any of it is known to
    exist. Deliberately permissive: validation is the resolver's job and a
    rejected leg is a dropped idea, not a 500.

    Lives here rather than beside the rest of the LLM schema so that
    importing the resolver (and testing it) does not pull in the Anthropic
    SDK."""

    kind: Kind
    strike: float = Field(gt=0)
    side: Literal["buy", "sell"]
    # None means the idea's own expiry; a calendar/diagonal names the long
    # leg's expiry here.
    expiry: date | None = None
    ratio: int = Field(default=1, ge=1, le=4)


@dataclass(frozen=True)
class ChainStrikes:
    """One expiry's strikes that survived the pre-filter, per kind,
    ascending. These -- and only these -- are what a proposal may be
    snapped onto."""

    expiry: date
    call: tuple[float, ...]
    put: tuple[float, ...]

    def of(self, kind: Kind) -> tuple[float, ...]:
        return self.call if kind == "call" else self.put

    @property
    def both(self) -> tuple[float, ...]:
        """Strikes quoted on the call *and* the put -- what a straddle and
        an iron butterfly's body need."""
        puts = set(self.put)
        return tuple(s for s in self.call if s in puts)


def _quote_is_offerable(quote: LegQuote | None, *, min_oi: int, max_spread_frac: float) -> bool:
    """Whether a contract is worth putting in front of the model: listed,
    tradable, quoted on both sides, not absurdly wide, and actually held by
    somebody."""
    if quote is None or not quote.tradable:
        return False
    if quote.bid is None or quote.ask is None or quote.bid <= 0 or quote.ask <= 0:
        return False
    if quote.mid is None or quote.mid <= 0:
        return False
    if quote.ask - quote.bid > max(max_spread_frac * quote.mid, MIN_ABS_SPREAD):
        return False
    return quote.open_interest >= min_oi


def _round(value: float | None, places: int) -> float | None:
    return None if value is None else round(value, places)


def _quote_payload(quote: LegQuote) -> dict:
    """The handful of numbers a structure decision actually turns on.
    Rounded, because full float precision on ~50 quotes is a lot of payload
    that carries no extra information."""
    return {
        "mid": _round(quote.mid, 2),
        "bid": _round(quote.bid, 2),
        "ask": _round(quote.ask, 2),
        "iv": _round(quote.iv, 3),
        "delta": _round(quote.delta, 3),
        "oi": quote.open_interest,
    }


def condense_chain(
    chain: Chain,
    *,
    max_strikes: int = MAX_STRIKES_PER_EXPIRY,
    min_oi: int = MIN_OPEN_INTEREST,
    max_spread_frac: float = MAX_SPREAD_FRACTION,
    strike_pct_range: float = STRIKE_PCT_RANGE,
) -> tuple[list[dict], ChainStrikes]:
    """(rows for the prompt, strikes the resolver may snap onto) for one
    expiry.

    Both come from one pass so they cannot disagree. A strike row survives
    when at least one of its two contracts is offerable; the row then
    carries only the side(s) that are, so the model never reads a mid off a
    contract it could not trade.
    """
    spot = chain.spot
    low = spot * (1 - strike_pct_range)
    high = spot * (1 + strike_pct_range)

    kept: list[tuple[float, LegQuote | None, LegQuote | None]] = []
    for row in chain.rows:
        if not low <= row.strike <= high:
            continue
        call = row.call if _quote_is_offerable(row.call, min_oi=min_oi, max_spread_frac=max_spread_frac) else None
        put = row.put if _quote_is_offerable(row.put, min_oi=min_oi, max_spread_frac=max_spread_frac) else None
        if call is None and put is None:
            continue
        kept.append((row.strike, call, put))

    # Trim from the outside in: the strikes nearest spot are the ones every
    # structure the ticket can build is made of.
    if len(kept) > max_strikes:
        kept = sorted(sorted(kept, key=lambda e: abs(e[0] - spot))[:max_strikes], key=lambda e: e[0])

    rows = [
        {
            "strike": strike,
            "call": _quote_payload(call) if call is not None else None,
            "put": _quote_payload(put) if put is not None else None,
        }
        for strike, call, put in kept
    ]
    strikes = ChainStrikes(
        expiry=chain.expiry,
        call=tuple(strike for strike, call, _ in kept if call is not None),
        put=tuple(strike for strike, _, put in kept if put is not None),
    )
    return rows, strikes


def _nearest(values: Sequence[float], target: float, what: str) -> float:
    if not values:
        raise IdeaUnresolvable(f"{what}: no quoted strikes to place this leg on")
    return min(values, key=lambda v: (abs(v - target), v))


def _above(values: Sequence[float], bound: float) -> float | None:
    return next((v for v in values if v > bound), None)


def _below(values: Sequence[float], bound: float) -> float | None:
    return next((v for v in reversed(values) if v < bound), None)


def _ascending(values: list[float], allowed: list[tuple[float, ...]], what: str) -> list[float]:
    """`values` forced strictly ascending, each leg staying on its own
    allowed strikes.

    Two legs snapping onto the same strike is the common failure -- the
    model asks for 178/177.5 where the chain only lists whole dollars. The
    first pass pushes the later legs outward, which is what one wants when
    there is room above; when the top of the chain is in the way it starts
    over and pulls the earlier legs inward instead. Only if neither leaves
    the structure intact is the idea genuinely unresolvable."""
    forward = list(values)
    blocked = False
    for i in range(1, len(forward)):
        if forward[i] > forward[i - 1]:
            continue
        nxt = _above(allowed[i], forward[i - 1])
        if nxt is None:
            blocked = True
            break
        forward[i] = nxt
    if not blocked:
        return forward

    backward = list(values)
    for i in range(len(backward) - 2, -1, -1):
        if backward[i] < backward[i + 1]:
            continue
        prev = _below(allowed[i], backward[i + 1])
        if prev is None:
            raise IdeaUnresolvable(f"{what}: no room in the chain to separate the strikes")
        backward[i] = prev
    return backward


def _strikes_for(strikes: dict[date, ChainStrikes], expiry: date, what: str) -> ChainStrikes:
    available = strikes.get(expiry)
    if available is None:
        raise IdeaUnresolvable(f"{what}: {expiry.isoformat()} is not one of the expiries offered")
    return available


def _leg(kind: Kind, strike: float, side: Literal["buy", "sell"], expiry: date, ratio: int = 1) -> TicketLeg:
    return TicketLeg(kind=kind, strike=strike, expiry=expiry, side=side, ratio=ratio)


def _snap_strike_fields(
    legs: list[ProposedLeg], strikes: dict[date, ChainStrikes], strategy: Strategy, expiry: date
) -> list[TicketLeg]:
    """The shapes SpreadTicket describes with strike fields. Kind and side
    come from the strategy (leg_specs), so only the proposed strikes are
    read -- and for the condor even their order is the strategy's, not the
    model's: puts are the two below, calls the two above, always."""
    what = strategy
    available = _strikes_for(strikes, expiry, what)
    proposed = sorted(leg.strike for leg in legs)

    if strategy in _SINGLE_KIND:
        if len(proposed) != 1:
            raise IdeaUnresolvable(f"{what}: expected one leg, got {len(proposed)}")
        kind = _SINGLE_KIND[strategy]
        return [_leg(kind, _nearest(available.of(kind), proposed[0], what), "buy", expiry)]

    if strategy == "iron_condor":
        if len(proposed) != 4:
            raise IdeaUnresolvable(f"{what}: expected four legs, got {len(proposed)}")
        puts, calls = available.put, available.call
        snapped = [
            _nearest(puts, proposed[0], what),
            _nearest(puts, proposed[1], what),
            _nearest(calls, proposed[2], what),
            _nearest(calls, proposed[3], what),
        ]
        pl, ps, cs, cl = _ascending(snapped, [puts, puts, calls, calls], what)
        return [
            _leg("put", pl, "buy", expiry),
            _leg("put", ps, "sell", expiry),
            _leg("call", cs, "sell", expiry),
            _leg("call", cl, "buy", expiry),
        ]

    if len(proposed) != 2:
        raise IdeaUnresolvable(f"{what}: expected two legs, got {len(proposed)}")
    kind = _VERTICAL_KIND[strategy]
    quoted = available.of(kind)
    lower, upper = _ascending(
        [_nearest(quoted, proposed[0], what), _nearest(quoted, proposed[1], what)], [quoted, quoted], what
    )
    long, short = (lower, upper) if strategy in _LONG_BELOW_SHORT else (upper, lower)
    return [_leg(kind, long, "buy", expiry), _leg(kind, short, "sell", expiry)]


def _one_of_kind(legs: list[ProposedLeg], kind: Kind, what: str) -> ProposedLeg:
    matching = [leg for leg in legs if leg.kind == kind]
    if len(matching) != 1:
        raise IdeaUnresolvable(f"{what}: expected exactly one {kind} leg, got {len(matching)}")
    return matching[0]


def _snap_time_spread(
    legs: list[ProposedLeg], strikes: dict[date, ChainStrikes], strategy: Strategy, expiry: date
) -> list[TicketLeg]:
    """Calendar and diagonal: the sold leg in the ticket's expiry, the
    bought leg further out. A calendar shares one strike across the two
    expiries, so it can only sit on a strike quoted in both."""
    what = strategy
    if len(legs) != 2:
        raise IdeaUnresolvable(f"{what}: expected two legs, got {len(legs)}")
    sold = [leg for leg in legs if leg.side == "sell"]
    bought = [leg for leg in legs if leg.side == "buy"]
    if len(sold) != 1 or len(bought) != 1:
        raise IdeaUnresolvable(f"{what}: one bought and one sold leg are required")
    short, long = sold[0], bought[0]
    if short.kind != long.kind:
        raise IdeaUnresolvable(f"{what}: both legs are the same kind")
    long_expiry = long.expiry
    if long_expiry is None or long_expiry <= expiry:
        raise IdeaUnresolvable(f"{what}: the bought leg needs an expiry after {expiry.isoformat()}")

    kind = short.kind
    near = _strikes_for(strikes, expiry, what).of(kind)
    far = _strikes_for(strikes, long_expiry, what).of(kind)

    if strategy == "calendar":
        shared = tuple(s for s in near if s in set(far))
        strike = _nearest(shared, (short.strike + long.strike) / 2, what)
        return [
            _leg(kind, strike, "sell", expiry),
            _leg(kind, strike, "buy", long_expiry),
        ]

    short_strike = _nearest(near, short.strike, what)
    long_strike = _nearest(far, long.strike, what)
    if short_strike == long_strike:
        # A diagonal that collapsed onto one strike is a calendar; move the
        # bought leg rather than change what was proposed.
        moved = _above(far, short_strike) or _below(far, short_strike)
        if moved is None:
            raise IdeaUnresolvable(f"{what}: the far expiry quotes only one usable strike")
        long_strike = moved
    return [
        _leg(kind, short_strike, "sell", expiry),
        _leg(kind, long_strike, "buy", long_expiry),
    ]


def _snap_leg_list(
    legs: list[ProposedLeg], strikes: dict[date, ChainStrikes], strategy: Strategy, expiry: date
) -> list[TicketLeg]:
    """The shapes SpreadTicket describes with an explicit legs list. Here
    the kinds and sides really are the model's -- a straddle is two bought
    legs of different kinds and nothing else -- but the ratios are not: a
    butterfly's body is sold twice by definition, never because the model
    said so."""
    what = strategy
    if strategy in TIME_STRATEGIES:
        return _snap_time_spread(legs, strikes, strategy, expiry)

    available = _strikes_for(strikes, expiry, what)

    if strategy in ("covered_call", "cash_secured_put"):
        if len(legs) != 1:
            raise IdeaUnresolvable(f"{what}: expected one leg, got {len(legs)}")
        kind: Kind = "call" if strategy == "covered_call" else "put"
        return [_leg(kind, _nearest(available.of(kind), legs[0].strike, what), "sell", expiry)]

    if strategy in ("long_straddle", "long_strangle"):
        if len(legs) != 2:
            raise IdeaUnresolvable(f"{what}: expected two legs, got {len(legs)}")
        put = _one_of_kind(legs, "put", what)
        call = _one_of_kind(legs, "call", what)
        if strategy == "long_straddle":
            strike = _nearest(available.both, (put.strike + call.strike) / 2, what)
            return [_leg("put", strike, "buy", expiry), _leg("call", strike, "buy", expiry)]
        put_strike, call_strike = _ascending(
            [_nearest(available.put, put.strike, what), _nearest(available.call, call.strike, what)],
            [available.put, available.call],
            what,
        )
        return [_leg("put", put_strike, "buy", expiry), _leg("call", call_strike, "buy", expiry)]

    if strategy in ("call_butterfly", "put_butterfly"):
        if len(legs) != 3:
            raise IdeaUnresolvable(f"{what}: expected three legs, got {len(legs)}")
        kind = "call" if strategy == "call_butterfly" else "put"
        quoted = available.of(kind)
        proposed = sorted(leg.strike for leg in legs)
        low, body, high = _ascending(
            [_nearest(quoted, s, what) for s in proposed], [quoted, quoted, quoted], what
        )
        return [
            _leg(kind, low, "buy", expiry),
            _leg(kind, body, "sell", expiry, ratio=2),
            _leg(kind, high, "buy", expiry),
        ]

    if strategy == "iron_butterfly":
        if len(legs) != 4:
            raise IdeaUnresolvable(f"{what}: expected four legs, got {len(legs)}")
        proposed = sorted(leg.strike for leg in legs)
        # The body is one strike carrying both short legs, so it has to be
        # quoted on both kinds; the two inner strikes are what the model
        # meant by it.
        body = _nearest(available.both, (proposed[1] + proposed[2]) / 2, what)
        # _nearest raises with a usable reason when a side has no strike
        # left outside the body, which is the "no room for a wing" case.
        wing_put = _nearest([s for s in available.put if s < body], proposed[0], what)
        wing_call = _nearest([s for s in available.call if s > body], proposed[3], what)
        return [
            _leg("put", wing_put, "buy", expiry),
            _leg("put", body, "sell", expiry),
            _leg("call", body, "sell", expiry),
            _leg("call", wing_call, "buy", expiry),
        ]

    raise IdeaUnresolvable(f"{what}: unsupported strategy")


def snap_legs(
    legs: list[ProposedLeg],
    strikes: dict[date, ChainStrikes],
    strategy: Strategy,
    expiry: date,
) -> list[TicketLeg]:
    """A proposal as legs that exist, in the canonical order
    SpreadTicket.leg_specs_full() uses. Raises IdeaUnresolvable when no
    repair keeps the structure intact."""
    if not legs:
        raise IdeaUnresolvable(f"{strategy}: no legs proposed")
    if strategy in LEGS_STRATEGIES:
        return _snap_leg_list(legs, strikes, strategy, expiry)
    return _snap_strike_fields(legs, strikes, strategy, expiry)


def ticket_from_legs(
    underlying: str,
    strategy: Strategy,
    expiry: date,
    legs: list[TicketLeg],
    qty: int,
    *,
    limit_price: float | None = None,
) -> SpreadTicket:
    """Snapped legs as the ticket the rest of the options stack already
    speaks -- strike fields or a legs list, whichever this strategy uses.
    SpreadTicket's own validation runs on construction, so anything the
    repair above got wrong surfaces here as a ValueError rather than as a
    422 in front of the user."""
    if strategy in LEGS_STRATEGIES:
        return SpreadTicket(
            underlying=underlying,
            strategy=strategy,
            expiry=expiry,
            qty=qty,
            legs=legs,
            limit_price=limit_price,
        )

    strikes = [leg.strike for leg in legs]
    common = {
        "underlying": underlying,
        "strategy": strategy,
        "expiry": expiry,
        "qty": qty,
        "limit_price": limit_price,
    }
    if strategy == "iron_condor":
        pl, ps, cs, cl = strikes
        return SpreadTicket(
            **common,
            put_long_strike=pl,
            put_short_strike=ps,
            call_short_strike=cs,
            call_long_strike=cl,
        )
    if len(strikes) == 1:
        return SpreadTicket(**common, long_strike=strikes[0])
    long, short = strikes
    return SpreadTicket(**common, long_strike=long, short_strike=short)
