"""The spread ticket and everything derived from it.

Kept apart from app.trading.models on purpose: OrderTicket is one symbol,
one side, one quantity in shares. A spread is two or four contracts that
only mean something together, and its "price" is a net debit or credit
per spread, not a price per share. Widening OrderTicket to carry both
would have every equity validator learn about legs; this file owns them
instead.

Strategies are the defined-risk ones: a single long call or put (level 2),
verticals and the iron condor (level 3). Leg order is canonical per strategy
(see leg_specs) so the pricing math can index strikes by position.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.options.chain import Chain, LegQuote
from app.options.occ import Kind, try_parse_occ
from app.trading.errors import OrderRejected

Strategy = Literal["long_call", "long_put", "bull_call", "bear_put", "bull_put", "bear_call", "iron_condor"]
Direction = Literal["debit", "credit"]
Side = Literal["buy", "sell"]
Intent = Literal["buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close"]

SINGLE_LEG_STRATEGIES: frozenset[str] = frozenset({"long_call", "long_put"})
DEBIT_STRATEGIES: frozenset[str] = frozenset({"long_call", "long_put", "bull_call", "bear_put"})
CREDIT_STRATEGIES: frozenset[str] = frozenset({"bull_put", "bear_call", "iron_condor"})

STRATEGY_LABELS: dict[str, str] = {
    "long_call": "Long call",
    "long_put": "Long put",
    "bull_call": "Bull call spread",
    "bear_put": "Bear put spread",
    "bull_put": "Bull put spread",
    "bear_call": "Bear call spread",
    "iron_condor": "Iron condor",
}

# The single kind a vertical is made of. Long leg below the short leg for
# the two bullish spreads, above it for the two bearish ones.
_VERTICAL_KIND: dict[str, Kind] = {
    "bull_call": "call",
    "bear_put": "put",
    "bull_put": "put",
    "bear_call": "call",
}
_LONG_BELOW_SHORT: frozenset[str] = frozenset({"bull_call", "bull_put"})
_SINGLE_KIND: dict[str, Kind] = {"long_call": "call", "long_put": "put"}

# What Alpaca requires: level 2 buys a call or a put outright, level 3 is
# needed for every spread.
OPTIONS_LEVEL_REQUIRED = 3
SINGLE_LEG_LEVEL_REQUIRED = 2


def options_level_required(strategy: str) -> int:
    return SINGLE_LEG_LEVEL_REQUIRED if strategy in SINGLE_LEG_STRATEGIES else OPTIONS_LEVEL_REQUIRED


class SpreadTicket(BaseModel):
    underlying: str = Field(min_length=1, max_length=12)
    strategy: Strategy
    expiry: date
    qty: int = Field(gt=0)
    # Verticals; a long call/put uses long_strike alone.
    long_strike: float | None = Field(default=None, gt=0)
    short_strike: float | None = Field(default=None, gt=0)
    # Iron condor: put wing below, call wing above.
    put_long_strike: float | None = Field(default=None, gt=0)
    put_short_strike: float | None = Field(default=None, gt=0)
    call_short_strike: float | None = Field(default=None, gt=0)
    call_long_strike: float | None = Field(default=None, gt=0)
    # Net price per spread the user will accept, always positive; whether it
    # is paid or received follows from the strategy. None means "the current
    # net mid", resolved server-side at preview time. Debit: the most to pay;
    # credit: the least to receive.
    limit_price: float | None = Field(default=None, gt=0)
    client_order_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def check_shape(self) -> "SpreadTicket":
        label = STRATEGY_LABELS[self.strategy]
        vertical = (self.long_strike, self.short_strike)
        condor = (
            self.put_long_strike,
            self.put_short_strike,
            self.call_short_strike,
            self.call_long_strike,
        )
        if self.strategy in SINGLE_LEG_STRATEGIES:
            if self.long_strike is None:
                raise ValueError(f"{label}: long_strike is required")
            if self.short_strike is not None or any(v is not None for v in condor):
                raise ValueError(f"{label}: only long_strike is used")
        elif self.strategy == "iron_condor":
            if any(v is None for v in condor):
                raise ValueError(f"{label}: put_long/put_short/call_short/call_long strikes are required")
            if any(v is not None for v in vertical):
                raise ValueError(f"{label}: long_strike/short_strike are not used")
            pl, ps, cs, cl = condor  # type: ignore[misc]
            if not (pl < ps < cs < cl):
                raise ValueError(
                    f"{label}: strikes must satisfy put_long < put_short < call_short < call_long"
                )
        else:
            if any(v is None for v in vertical):
                raise ValueError(f"{label}: long_strike and short_strike are required")
            if any(v is not None for v in condor):
                raise ValueError(f"{label}: iron condor strike fields are not used")
            long, short = vertical  # type: ignore[misc]
            if long == short:
                raise ValueError(f"{label}: long and short strike must differ")
            if self.strategy in _LONG_BELOW_SHORT and not long < short:
                raise ValueError(f"{label}: the long strike must be below the short strike")
            if self.strategy not in _LONG_BELOW_SHORT and not long > short:
                raise ValueError(f"{label}: the long strike must be above the short strike")
        return self

    @property
    def direction(self) -> Direction:
        return "debit" if self.strategy in DEBIT_STRATEGIES else "credit"

    @property
    def strikes(self) -> tuple[float, ...]:
        """Canonical strike order for pricing.spread_risk: (long,) for a
        single leg, (long, short) for a vertical, (put_long, put_short,
        call_short, call_long) for a condor."""
        if self.strategy in SINGLE_LEG_STRATEGIES:
            return (self.long_strike,)  # type: ignore[return-value]
        if self.strategy == "iron_condor":
            return (
                self.put_long_strike,  # type: ignore[return-value]
                self.put_short_strike,
                self.call_short_strike,
                self.call_long_strike,
            )
        return (self.long_strike, self.short_strike)  # type: ignore[return-value]

    def leg_specs(self) -> list[tuple[Kind, float, Side]]:
        """(kind, strike, side) per leg, in canonical order."""
        if self.strategy in SINGLE_LEG_STRATEGIES:
            return [(_SINGLE_KIND[self.strategy], self.strikes[0], "buy")]
        if self.strategy == "iron_condor":
            pl, ps, cs, cl = self.strikes
            return [("put", pl, "buy"), ("put", ps, "sell"), ("call", cs, "sell"), ("call", cl, "buy")]
        kind = _VERTICAL_KIND[self.strategy]
        long, short = self.strikes
        return [(kind, long, "buy"), (kind, short, "sell")]


class SpreadLeg(BaseModel):
    symbol: str
    kind: Kind
    strike: float
    expiry: date
    side: Side
    position_intent: Intent
    ratio_qty: int = 1
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    delta: float | None = None


class ResolvedSpread(BaseModel):
    underlying: str
    strategy: Strategy
    expiry: date
    qty: int
    direction: Direction
    legs: list[SpreadLeg]
    spot: float
    width: float
    # Positive numbers, per spread. net_mid is what the market quotes the
    # package at right now; net_natural is the bid/ask-crossing price (the
    # worst fill); limit_price is what the order will carry.
    net_mid: float
    net_natural: float | None
    limit_price: float
    # Signed the way Alpaca's MLEG limit wants it: +debit / -credit.
    alpaca_limit_price: float
    # None means unlimited (a long call).
    max_profit: float | None
    max_loss: float
    breakevens: list[float]
    collateral: float
    options_buying_power: float | None
    dte: int
    options_level: int | None
    account: str
    warnings: list[str] = Field(default_factory=list)
    client_order_id: str | None = None


class CloseLeg(BaseModel):
    """One held contract: its symbol and the *signed* quantity as held
    (positive long, negative short) -- the shape Alpaca's positions come in."""

    symbol: str = Field(min_length=15, max_length=21)
    qty: int

    @model_validator(mode="after")
    def check_qty(self) -> "CloseLeg":
        if self.qty == 0:
            raise ValueError("a held leg cannot have qty 0")
        if try_parse_occ(self.symbol) is None:
            raise ValueError(f"not an OCC option symbol: {self.symbol}")
        return self


class CloseSpreadRequest(BaseModel):
    legs: list[CloseLeg] = Field(min_length=1, max_length=4)
    # Spreads to close; at most what every leg can support.
    qty: int = Field(gt=0)
    # Positive net price per spread, or None for the current mid.
    limit_price: float | None = Field(default=None, gt=0)
    client_order_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def check_legs(self) -> "CloseSpreadRequest":
        symbols = [leg.symbol for leg in self.legs]
        if len(set(symbols)) != len(symbols):
            raise ValueError("each leg symbol may appear once")
        if self.qty > min(abs(leg.qty) for leg in self.legs):
            raise ValueError("qty exceeds what is held on at least one leg")
        return self


class TriggerCreate(BaseModel):
    underlying: str = Field(min_length=1, max_length=12)
    expiry: date
    legs: list[CloseLeg] = Field(min_length=1, max_length=4)
    qty: int = Field(gt=0)
    # Bounds on the underlying's last price...
    close_below: float | None = Field(default=None, gt=0)
    close_above: float | None = Field(default=None, gt=0)
    # ...and/or on the position's own mark per share: the mid of closing
    # the package, as a positive number (a long call's premium; what it
    # costs to buy a credit spread back). premium_below is a long's stop
    # or a credit spread's take-profit; premium_above the reverse.
    premium_below: float | None = Field(default=None, gt=0)
    premium_above: float | None = Field(default=None, gt=0)

    @property
    def has_premium_bounds(self) -> bool:
        return self.premium_below is not None or self.premium_above is not None

    @model_validator(mode="after")
    def check_bounds(self) -> "TriggerCreate":
        if all(v is None for v in (self.close_below, self.close_above, self.premium_below, self.premium_above)):
            raise ValueError("set a bound on the underlying price (close_below/close_above) or the premium")
        if (
            self.close_below is not None
            and self.close_above is not None
            and not self.close_below < self.close_above
        ):
            raise ValueError("close_below must be below close_above")
        if (
            self.premium_below is not None
            and self.premium_above is not None
            and not self.premium_below < self.premium_above
        ):
            raise ValueError("premium_below must be below premium_above")
        if self.qty > min(abs(leg.qty) for leg in self.legs):
            raise ValueError("qty exceeds what is held on at least one leg")
        return self


def _leg_from_quote(quote: LegQuote, side: Side, intent: Intent) -> SpreadLeg:
    return SpreadLeg(
        symbol=quote.symbol,
        kind=quote.kind,
        strike=quote.strike,
        expiry=quote.expiry,
        side=side,
        position_intent=intent,
        bid=quote.bid,
        ask=quote.ask,
        mid=quote.mid,
        delta=quote.delta,
    )


def resolve_legs(ticket: SpreadTicket, chain: Chain) -> list[SpreadLeg]:
    """The ticket's strikes looked up in the live chain -- contract symbols
    come from there, never from format_occ, so adjusted roots and
    non-tradable contracts are handled by the data rather than guessed."""
    if chain.expiry != ticket.expiry:
        raise OrderRejected(
            f"Chain is for {chain.expiry.isoformat()}, ticket is for {ticket.expiry.isoformat()}",
            field="expiry",
        )
    legs: list[SpreadLeg] = []
    for kind, strike, side in ticket.leg_specs():
        quote = chain.quote(kind, strike)
        if quote is None:
            raise OrderRejected(
                f"No {kind} at strike {strike:g} for {ticket.underlying} {ticket.expiry.isoformat()}",
                field="strikes",
            )
        if not quote.tradable:
            raise OrderRejected(f"{quote.symbol} is not tradable at Alpaca", field="strikes")
        intent: Intent = "buy_to_open" if side == "buy" else "sell_to_open"
        legs.append(_leg_from_quote(quote, side, intent))
    return legs


def closing_legs(held: list[CloseLeg]) -> list[SpreadLeg]:
    """The reverse of what is held: a long leg is sold to close, a short leg
    bought to close. Quotes are filled in afterwards (see the service)."""
    legs: list[SpreadLeg] = []
    for leg in held:
        parsed = try_parse_occ(leg.symbol)
        if parsed is None:
            raise OrderRejected(f"Not an option symbol: {leg.symbol}", field="legs")
        if leg.qty > 0:
            side, intent = "sell", "sell_to_close"
        else:
            side, intent = "buy", "buy_to_close"
        legs.append(
            SpreadLeg(
                symbol=parsed.symbol,
                kind=parsed.kind,
                strike=parsed.strike,
                expiry=parsed.expiry,
                side=side,
                position_intent=intent,
            )
        )
    return legs
