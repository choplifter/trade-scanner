"""The spread ticket and everything derived from it.

Kept apart from app.trading.models on purpose: OrderTicket is one symbol,
one side, one quantity in shares. A spread is two to four contracts that
only mean something together, and its "price" is a net debit or credit
per spread, not a price per share. Widening OrderTicket to carry both
would have every equity validator learn about legs; this file owns them
instead.

Two ways to describe the legs. The original strategies (long call/put,
the four verticals, the iron condor) use the fixed strike fields the
frontend has always sent -- long_strike/short_strike, put_*/call_*. The
strategies added later (straddle, strangle, butterflies, calendar,
diagonal, covered call, cash-secured put) send an explicit `legs` list:
kind, strike, expiry (for the two-expiry shapes), side and ratio (the
butterfly's body is 2). Either way `leg_specs_full()` gives the canonical
legs, which is what pricing and the request builder read.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.options.chain import Chain, LegQuote
from app.options.occ import Kind, try_parse_occ
from app.trading.errors import OrderRejected

Strategy = Literal[
    "long_call",
    "long_put",
    "bull_call",
    "bear_put",
    "bull_put",
    "bear_call",
    "iron_condor",
    "long_straddle",
    "long_strangle",
    "call_butterfly",
    "put_butterfly",
    "iron_butterfly",
    "calendar",
    "diagonal",
    "covered_call",
    "cash_secured_put",
]
Direction = Literal["debit", "credit"]
Side = Literal["buy", "sell"]
Intent = Literal["buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close"]

# One contract, bought (level 2) or sold against cover (level 1).
SINGLE_LEG_STRATEGIES: frozenset[str] = frozenset({"long_call", "long_put", "covered_call", "cash_secured_put"})
# Sold against held shares / cash rather than against another contract.
INCOME_STRATEGIES: frozenset[str] = frozenset({"covered_call", "cash_secured_put"})
# Two expiries: the short leg in the nearer one.
TIME_STRATEGIES: frozenset[str] = frozenset({"calendar", "diagonal"})
# Strategies described by an explicit `legs` list rather than strike fields.
LEGS_STRATEGIES: frozenset[str] = frozenset(
    {
        "long_straddle",
        "long_strangle",
        "call_butterfly",
        "put_butterfly",
        "iron_butterfly",
        "calendar",
        "diagonal",
        "covered_call",
        "cash_secured_put",
    }
)
DEBIT_STRATEGIES: frozenset[str] = frozenset(
    {
        "long_call",
        "long_put",
        "bull_call",
        "bear_put",
        "long_straddle",
        "long_strangle",
        "call_butterfly",
        "put_butterfly",
        "calendar",
        "diagonal",
    }
)
CREDIT_STRATEGIES: frozenset[str] = frozenset(
    {"bull_put", "bear_call", "iron_condor", "iron_butterfly", "covered_call", "cash_secured_put"}
)

STRATEGY_LABELS: dict[str, str] = {
    "long_call": "Long call",
    "long_put": "Long put",
    "bull_call": "Bull call spread",
    "bear_put": "Bear put spread",
    "bull_put": "Bull put spread",
    "bear_call": "Bear call spread",
    "iron_condor": "Iron condor",
    "long_straddle": "Long straddle",
    "long_strangle": "Long strangle",
    "call_butterfly": "Call butterfly",
    "put_butterfly": "Put butterfly",
    "iron_butterfly": "Iron butterfly",
    "calendar": "Calendar spread",
    "diagonal": "Diagonal spread",
    "covered_call": "Covered call",
    "cash_secured_put": "Cash-secured put",
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

# What Alpaca requires: level 1 writes a covered call or a cash-secured
# put, level 2 buys a call or a put outright, level 3 is needed for every
# spread.
OPTIONS_LEVEL_REQUIRED = 3
SINGLE_LEG_LEVEL_REQUIRED = 2
INCOME_LEVEL_REQUIRED = 1


def options_level_required(strategy: str) -> int:
    if strategy in INCOME_STRATEGIES:
        return INCOME_LEVEL_REQUIRED
    if strategy in SINGLE_LEG_STRATEGIES:
        return SINGLE_LEG_LEVEL_REQUIRED
    return OPTIONS_LEVEL_REQUIRED


class TicketLeg(BaseModel):
    """One leg as the ticket describes it. `expiry` None means the ticket's
    own expiry; `ratio` is the leg's contracts per spread (a butterfly's
    body is 2)."""

    kind: Kind
    strike: float = Field(gt=0)
    expiry: date | None = None
    side: Side
    ratio: int = Field(default=1, ge=1, le=4)


def _strikes(legs: list[TicketLeg]) -> list[float]:
    return [leg.strike for leg in legs]


class SpreadTicket(BaseModel):
    underlying: str = Field(min_length=1, max_length=12)
    strategy: Strategy
    # The expiry of every leg for the single-expiry shapes; the *short*
    # leg's expiry for a calendar/diagonal.
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
    # The explicit legs of the later strategies (see the module docstring).
    legs: list[TicketLeg] | None = Field(default=None, min_length=1, max_length=4)
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
        if self.strategy in LEGS_STRATEGIES:
            if any(v is not None for v in vertical) or any(v is not None for v in condor):
                raise ValueError(f"{label}: describe the legs with `legs`, not strike fields")
            if not self.legs:
                raise ValueError(f"{label}: `legs` are required")
            self._check_legs(label)
            return self
        if self.legs is not None:
            raise ValueError(f"{label}: uses strike fields, not `legs`")
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

    def _check_legs(self, label: str) -> None:
        legs = self.legs or []
        kinds = [leg.kind for leg in legs]
        sides = [leg.side for leg in legs]
        expiries = [leg.expiry or self.expiry for leg in legs]
        s = self.strategy

        def need(n: int) -> None:
            if len(legs) != n:
                raise ValueError(f"{label}: exactly {n} legs are required")

        if s in ("long_straddle", "long_strangle"):
            need(2)
            if sorted(kinds) != ["call", "put"] or sides != ["buy", "buy"]:
                raise ValueError(f"{label}: one bought put and one bought call")
            put = next(leg for leg in legs if leg.kind == "put")
            call = next(leg for leg in legs if leg.kind == "call")
            if s == "long_straddle" and put.strike != call.strike:
                raise ValueError(f"{label}: put and call share one strike")
            if s == "long_strangle" and not put.strike < call.strike:
                raise ValueError(f"{label}: the put strike must be below the call strike")
        elif s in ("call_butterfly", "put_butterfly"):
            need(3)
            kind = "call" if s == "call_butterfly" else "put"
            if any(k != kind for k in kinds):
                raise ValueError(f"{label}: all three legs are {kind}s")
            ordered = sorted(legs, key=lambda leg: leg.strike)
            low, body, high = ordered
            if not low.strike < body.strike < high.strike:
                raise ValueError(f"{label}: three distinct strikes, low < body < high")
            if (low.side, body.side, high.side) != ("buy", "sell", "buy") or body.ratio != 2 or low.ratio != 1 or high.ratio != 1:
                raise ValueError(f"{label}: buy the wings (ratio 1), sell the body (ratio 2)")
        elif s == "iron_butterfly":
            need(4)
            puts = sorted((leg for leg in legs if leg.kind == "put"), key=lambda leg: leg.strike)
            calls = sorted((leg for leg in legs if leg.kind == "call"), key=lambda leg: leg.strike)
            if len(puts) != 2 or len(calls) != 2:
                raise ValueError(f"{label}: two puts and two calls")
            if (puts[0].side, puts[1].side, calls[0].side, calls[1].side) != ("buy", "sell", "sell", "buy"):
                raise ValueError(f"{label}: buy the outer put and call, sell the inner put and call")
            if puts[1].strike != calls[0].strike:
                raise ValueError(f"{label}: the short put and short call share the body strike")
            if not puts[0].strike < puts[1].strike < calls[1].strike:
                raise ValueError(f"{label}: put wing below the body, call wing above")
        elif s in TIME_STRATEGIES:
            need(2)
            if kinds[0] != kinds[1]:
                raise ValueError(f"{label}: both legs are the same kind")
            if sorted(sides) != ["buy", "sell"]:
                raise ValueError(f"{label}: one bought, one sold leg")
            short = next(leg for leg in legs if leg.side == "sell")
            long = next(leg for leg in legs if leg.side == "buy")
            short_exp = short.expiry or self.expiry
            long_exp = long.expiry or self.expiry
            if not short_exp < long_exp:
                raise ValueError(f"{label}: the sold leg expires before the bought leg")
            if short_exp != self.expiry:
                raise ValueError(f"{label}: `expiry` is the short leg's expiry")
            if s == "calendar" and short.strike != long.strike:
                raise ValueError(f"{label}: both legs share one strike (use a diagonal for two)")
            if s == "diagonal" and short.strike == long.strike:
                raise ValueError(f"{label}: two different strikes (use a calendar for one)")
        elif s == "covered_call":
            need(1)
            if kinds != ["call"] or sides != ["sell"]:
                raise ValueError(f"{label}: one sold call")
        elif s == "cash_secured_put":
            need(1)
            if kinds != ["put"] or sides != ["sell"]:
                raise ValueError(f"{label}: one sold put")
        if any(leg.ratio != 1 for leg in legs) and s not in ("call_butterfly", "put_butterfly"):
            raise ValueError(f"{label}: ratio legs are only used by butterflies")
        if any(exp != self.expiry for exp in expiries) and s not in TIME_STRATEGIES:
            raise ValueError(f"{label}: every leg uses the ticket's expiry")

    @property
    def direction(self) -> Direction:
        return "debit" if self.strategy in DEBIT_STRATEGIES else "credit"

    @property
    def expiries(self) -> list[date]:
        """Every expiry a leg uses, ascending."""
        return sorted({leg.expiry for leg in self.leg_specs_full()})

    @property
    def strikes(self) -> tuple[float, ...]:
        """Canonical strike order for pricing.spread_risk: (long,) for a
        single leg, (long, short) for a vertical, (put_long, put_short,
        call_short, call_long) for a condor, and for the leg-list
        strategies the strikes in leg_specs_full() order."""
        if self.strategy in LEGS_STRATEGIES:
            return tuple(_strikes(self.leg_specs_full()))
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

    def leg_specs_full(self) -> list[TicketLeg]:
        """Every leg with its expiry and ratio, in canonical order:
        straddle/strangle put then call; butterflies low, body, high;
        iron butterfly put long, put short, call short, call long;
        calendar/diagonal short then long; the older shapes as leg_specs."""
        s = self.strategy
        if s in LEGS_STRATEGIES:
            legs = [leg.model_copy(update={"expiry": leg.expiry or self.expiry}) for leg in self.legs or []]
            if s in ("long_straddle", "long_strangle"):
                return sorted(legs, key=lambda leg: 0 if leg.kind == "put" else 1)
            if s in ("call_butterfly", "put_butterfly"):
                return sorted(legs, key=lambda leg: leg.strike)
            if s == "iron_butterfly":
                puts = sorted((leg for leg in legs if leg.kind == "put"), key=lambda leg: leg.strike)
                calls = sorted((leg for leg in legs if leg.kind == "call"), key=lambda leg: leg.strike)
                return [puts[0], puts[1], calls[0], calls[1]]
            if s in TIME_STRATEGIES:
                return sorted(legs, key=lambda leg: 0 if leg.side == "sell" else 1)
            return legs
        return [
            TicketLeg(kind=kind, strike=strike, expiry=self.expiry, side=side, ratio=1)
            for kind, strike, side in self.leg_specs()
        ]

    def leg_specs(self) -> list[tuple[Kind, float, Side]]:
        """(kind, strike, side) per leg, in canonical order -- the
        single-expiry, ratio-1 view. The leg-list strategies report their
        legs the same way (a butterfly's body appears once; its ratio is
        on leg_specs_full)."""
        if self.strategy in LEGS_STRATEGIES:
            return [(leg.kind, leg.strike, leg.side) for leg in self.leg_specs_full()]
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
    gamma: float | None = None
    theta: float | None = None
    iv: float | None = None
    # Replay: the time of the bar the price came from (see LegQuote.last_at).
    last_at: datetime | None = None


class Coverage(BaseModel):
    """What a covered call / cash-secured put is written against."""

    kind: Literal["shares", "cash"]
    have: float
    need: float
    ok: bool


class PayoffLegOut(BaseModel):
    """One leg as the payoff valued it -- what the browser needs to value
    it again at another hour or IV (see payoff_curve)."""

    kind: Literal["call", "put", "stock"]
    strike: float
    side: Literal["buy", "sell"]
    ratio: int = 1
    expiry: date | None = None
    iv: float | None = None


class Payoff(BaseModel):
    """P&L per position (x 100 x qty) over a grid of underlying prices --
    see app.options.payoff."""

    prices: list[float]
    at_expiry: list[float]
    today: list[float] | None
    breakevens: list[float]
    max_profit: float | None
    max_loss: float | None
    spot: float
    expiry: date
    multiplier: int
    legs: list[PayoffLegOut] = Field(default_factory=list)
    # Per share, signed like the ticket: positive paid, negative received.
    net_price: float | None = None
    # The moment the today curve was valued at.
    as_of: datetime | None = None


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
    # None means unbounded on the grid used (see payoff).
    max_loss: float | None
    breakevens: list[float]
    collateral: float
    options_buying_power: float | None
    dte: int
    options_level: int | None
    account: str
    warnings: list[str] = Field(default_factory=list)
    client_order_id: str | None = None
    coverage: Coverage | None = None
    payoff: Payoff | None = None


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


class PayoffRequest(BaseModel):
    """The risk chart of a held position: its legs as held, how many of the
    package, and the net entry per share (positive was paid)."""

    legs: list[CloseLeg] = Field(min_length=1, max_length=4)
    qty: int = Field(gt=0)
    net_entry: float


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


def _leg_from_quote(quote: LegQuote, side: Side, intent: Intent, ratio: int = 1) -> SpreadLeg:
    return SpreadLeg(
        symbol=quote.symbol,
        kind=quote.kind,
        strike=quote.strike,
        expiry=quote.expiry,
        side=side,
        position_intent=intent,
        ratio_qty=ratio,
        bid=quote.bid,
        ask=quote.ask,
        mid=quote.mid,
        delta=quote.delta,
        gamma=quote.gamma,
        theta=quote.theta,
        iv=quote.iv,
        last_at=quote.last_at,
    )


def resolve_legs(ticket: SpreadTicket, chains: Chain | dict[date, Chain]) -> list[SpreadLeg]:
    """The ticket's strikes looked up in the live chain(s) -- one chain per
    expiry the ticket uses. Contract symbols come from there, never from
    format_occ, so adjusted roots and non-tradable contracts are handled by
    the data rather than guessed."""
    by_expiry: dict[date, Chain] = {chains.expiry: chains} if isinstance(chains, Chain) else dict(chains)
    legs: list[SpreadLeg] = []
    for spec in ticket.leg_specs_full():
        expiry = spec.expiry or ticket.expiry
        chain = by_expiry.get(expiry)
        if chain is None:
            raise OrderRejected(f"No chain loaded for {expiry.isoformat()}", field="expiry")
        quote = chain.quote(spec.kind, spec.strike)
        if quote is None:
            raise OrderRejected(
                f"No {spec.kind} at strike {spec.strike:g} for {ticket.underlying} {expiry.isoformat()}",
                field="strikes",
            )
        if not quote.tradable:
            raise OrderRejected(f"{quote.symbol} is not tradable at Alpaca", field="strikes")
        intent: Intent = "buy_to_open" if spec.side == "buy" else "sell_to_open"
        legs.append(_leg_from_quote(quote, spec.side, intent, spec.ratio))
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
