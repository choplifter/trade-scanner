"""The order ticket, and everything that must be true before it is sent.

Validation is split in two on purpose. Pydantic handles what can be judged
from the ticket alone -- a limit order needs a limit price, a stop order a
trigger, exactly one sizing mode. `resolve_ticket` handles what needs the
account, the market and the settings: the computed quantity, the ceilings,
buying power, and whether the prices make sense against the last trade --
bracket legs on the correct side of the *expected fill*, a stop trigger
on the far side of the market, a limit that would fill on arrival.

order_class is derived here and never accepted from the client, so a
malformed combination of legs is not expressible over the wire.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.trading.errors import OrderRejected
from app.trading.sizing import SizingResult, assert_within_limits, risk_amount_for, shares_for_risk

Side = Literal["buy", "sell"]
# stop / stop_limit are *entry* triggers -- "buy me in when it trades up
# through X" -- and are distinct from the stop_loss_price leg, which is the
# protective exit attached to whatever entry fills. The distinction exists
# because a breakout entry typed as a limit above the market is not a
# resting order at all: a buy limit means "this price or better", so it
# fills immediately at the ask. Observed live on DAIC: six buy limits at
# 4.50-5.00 against a 4.2 market, every one filled on the spot.
OrderType = Literal["market", "limit", "stop", "stop_limit"]

# Which of the two entry prices each type carries. Kept as data so the
# ticket validator, resolve_ticket and the request builder all agree.
_NEEDS_LIMIT = frozenset({"limit", "stop_limit"})
_NEEDS_STOP = frozenset({"stop", "stop_limit"})


class RiskSizing(BaseModel):
    """Size from a stop rather than a share count.

    Exactly one of risk_amount / risk_pct_of_equity -- "1% and also $500" has
    no meaning, and picking one silently would size a trade the user did not
    describe.
    """

    stop_price: float = Field(gt=0)
    risk_amount: float | None = Field(default=None, gt=0)
    risk_pct_of_equity: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def exactly_one_risk_basis(self) -> "RiskSizing":
        provided = [self.risk_amount is not None, self.risk_pct_of_equity is not None]
        if sum(provided) != 1:
            raise ValueError("Provide exactly one of risk_amount or risk_pct_of_equity.")
        return self


class OrderTicket(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    side: Side
    order_type: OrderType = "market"
    # None means "decide from the ticket" -- see resolve_time_in_force. Day
    # and gtc are the only two Alpaca accepts on a bracket, and every ticket
    # here can become one.
    time_in_force: Literal["day", "gtc"] | None = None

    # Exactly one of qty / risk, for the same reason as above.
    qty: int | None = Field(default=None, gt=0)
    risk: RiskSizing | None = None

    limit_price: float | None = Field(default=None, gt=0)
    # The entry trigger of a stop / stop_limit order. Named after Alpaca's
    # own field so the orders table and the ticket say the same thing; it is
    # NOT the protective stop -- that is stop_loss_price (or risk.stop_price).
    stop_price: float | None = Field(default=None, gt=0)
    take_profit_price: float | None = Field(default=None, gt=0)
    stop_loss_price: float | None = Field(default=None, gt=0)

    # Client-generated so a retry after a timeout is the *same* order rather
    # than a second one -- Alpaca rejects a duplicate id. Minting it
    # server-side would defeat that, since a retry would arrive with a new one.
    client_order_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def resolve_time_in_force(self) -> "OrderTicket":
        """Default a protected ticket to gtc, an unprotected one to day.

        A day order's legs die at the close while the position they were
        protecting does not. Observed on this account: four positions held
        overnight, every one of them with its take-profit `expired` and its
        stop-loss `canceled` at the previous close, leaving them naked. A
        stop that silently stops existing is worse than no stop at all,
        because the position still looks protected on the ticket that placed
        it.

        Alpaca has no per-leg time_in_force -- TakeProfitRequest carries only
        a limit price, StopLossRequest a stop price -- so "gtc legs behind a
        day entry" is not expressible, and the whole bracket goes gtc. An
        explicit choice from the caller always wins.
        """
        if self.time_in_force is None:
            self.time_in_force = "gtc" if self.has_protective_exit else "day"
        return self

    @model_validator(mode="after")
    def check_shape(self) -> "OrderTicket":
        if (self.qty is None) == (self.risk is None):
            raise ValueError("Provide exactly one of qty or risk.")
        needs_limit = self.order_type in _NEEDS_LIMIT
        needs_stop = self.order_type in _NEEDS_STOP
        if needs_limit and self.limit_price is None:
            raise ValueError(f"A {self.order_type} order needs a limit_price.")
        if not needs_limit and self.limit_price is not None:
            raise ValueError(f"A {self.order_type} order cannot carry a limit_price.")
        if needs_stop and self.stop_price is None:
            raise ValueError(f"A {self.order_type} order needs a stop_price (its trigger).")
        if not needs_stop and self.stop_price is not None:
            raise ValueError(f"A {self.order_type} order cannot carry a stop_price.")
        if self.order_type == "stop_limit":
            _check_stop_limit_shape(self.side, self.stop_price, self.limit_price)
        return self

    @property
    def has_protective_exit(self) -> bool:
        """Whether this ticket places something that closes the position by
        itself.

        Risk sizing counts even with no explicit stop_loss_price: resolve_ticket
        adopts the sizing stop as the stop-loss leg, so a ticket sized from a
        stop always ends up placing one.
        """
        return (
            self.take_profit_price is not None
            or self.stop_loss_price is not None
            or self.risk is not None
        )

    @property
    def order_class(self) -> str:
        """Derived, never client-supplied. Both legs is a bracket, one is an
        OTO, neither is a plain order."""
        legs = [self.take_profit_price is not None, self.stop_loss_price is not None]
        if all(legs):
            return "bracket"
        if any(legs):
            return "oto"
        return "simple"


class ResolvedOrder(BaseModel):
    """What the ticket actually means, once priced and sized."""

    symbol: str
    side: Side
    order_type: OrderType
    time_in_force: str
    order_class: str
    qty: int
    entry_reference: float
    notional: float
    limit_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    risk_amount: float | None = None
    risk_per_share: float | None = None
    risk_pct_of_equity: float | None = None
    client_order_id: str | None = None
    # Things worth knowing that are not refusals -- a marketable limit is a
    # legitimate order (a capped market order), it just is not a *resting*
    # one, and the ticket should say so before the user finds out from the
    # fill. Rendered by the preview and repeated on the confirmation.
    warnings: list[str] = Field(default_factory=list)


def _check_stop_limit_shape(side: str, stop: float | None, limit: float | None) -> None:
    """A buy stop-limit's limit sits at or above its trigger; a sell's at or
    below. The other way round is an order that can only fill on a
    pullback *after* triggering, which nobody means by "stop-limit" and
    which the broker refuses -- but late, and in its own wording."""
    if stop is None or limit is None:
        return
    if side == "buy" and limit < stop:
        raise ValueError(
            f"A buy stop-limit's limit must be at or above its stop trigger "
            f"({limit:.4f} is below {stop:.4f})."
        )
    if side == "sell" and limit > stop:
        raise ValueError(
            f"A sell stop-limit's limit must be at or below its stop trigger "
            f"({limit:.4f} is above {stop:.4f})."
        )


def _check_trigger_side(ticket: OrderTicket, reference_price: float | None) -> None:
    """A stop entry must sit on the far side of the market, or it is not a
    trigger: a buy stop at or below the last trade fires the moment it
    arrives (or is refused by the broker), which is the same immediate fill
    the user reached for a stop order to avoid.
    """
    trigger = ticket.stop_price
    if trigger is None or reference_price is None:
        return
    if ticket.side == "buy" and trigger <= reference_price:
        raise OrderRejected(
            f"A buy stop triggers when price rises to it, so it must sit above the current "
            f"price ({trigger:.4f} is not above {reference_price:.4f}). To buy at or below "
            f"the market use a limit order.",
            field="stop_price",
        )
    if ticket.side == "sell" and trigger >= reference_price:
        raise OrderRejected(
            f"A sell stop triggers when price falls to it, so it must sit below the current "
            f"price ({trigger:.4f} is not below {reference_price:.4f}). To sell at or above "
            f"the market use a limit order.",
            field="stop_price",
        )


def _expected_fill(ticket: OrderTicket, entry: float, reference_price: float | None) -> float:
    """Where the entry will actually fill, as opposed to the price it is
    written at.

    They differ for a marketable limit: a buy limit above the market fills
    at the ask, not at the limit. Checking the bracket legs against the
    limit instead let a 4.50 stop-loss through under a 5.00 buy limit on a
    4.20 market -- the entry filled at 4.20 and the stop, now *above* the
    position, fired on the next tick. A stop entry fills at its trigger by
    definition, so nothing changes there.
    """
    if ticket.order_type != "limit" or reference_price is None:
        return entry
    return min(entry, reference_price) if ticket.side == "buy" else max(entry, reference_price)


def _marketable_warning(ticket: OrderTicket, reference_price: float | None) -> str | None:
    """A limit on the wrong side of the market is a capped market order,
    not a resting one. Not a refusal -- "buy now, but pay no more than X"
    is a real thing to want -- but the difference between that and "buy
    when it gets to X" is the whole reason stop entries exist, so say it."""
    limit = ticket.limit_price
    if ticket.order_type != "limit" or limit is None or reference_price is None:
        return None
    if ticket.side == "buy" and limit >= reference_price:
        return (
            f"Limit {limit:.2f} is at or above the current price {reference_price:.2f}: a buy "
            f'limit means "this price or lower", so this fills immediately at about '
            f"{reference_price:.2f} rather than waiting for {limit:.2f}. For a breakout entry "
            f"that waits for {limit:.2f}, use a Stop order."
        )
    if ticket.side == "sell" and limit <= reference_price:
        return (
            f"Limit {limit:.2f} is at or below the current price {reference_price:.2f}: a sell "
            f'limit means "this price or higher", so this fills immediately at about '
            f"{reference_price:.2f} rather than waiting for {limit:.2f}. For a breakdown entry "
            f"that waits for {limit:.2f}, use a Stop order."
        )
    return None


def _check_bracket_sides(ticket: OrderTicket, fill: float) -> None:
    """Take-profit above and stop below for a buy; inverted for a sell.

    Judged against the *expected fill*, not the written entry -- see
    _expected_fill. Alpaca rejects these too, but late and with an opaque
    message. Refusing here names the leg that is wrong.
    """
    tp, sl = ticket.take_profit_price, ticket.stop_loss_price
    if sl is None and ticket.risk is not None:
        # The sizing stop becomes the stop-loss leg (see resolve_ticket), so
        # it is held to the same rule -- otherwise a risk-sized ticket could
        # place the exact stop an explicit one would be refused for.
        sl = ticket.risk.stop_price
    if ticket.side == "buy":
        if tp is not None and tp <= fill:
            raise OrderRejected(
                f"A buy's take-profit must sit above the entry ({tp:.4f} is not above {fill:.4f}).",
                field="take_profit_price",
            )
        if sl is not None and sl >= fill:
            raise OrderRejected(
                f"A buy's stop-loss must sit below the entry ({sl:.4f} is not below {fill:.4f}).",
                field="stop_loss_price",
            )
    else:
        if tp is not None and tp >= fill:
            raise OrderRejected(
                f"A sell's take-profit must sit below the entry ({tp:.4f} is not below {fill:.4f}).",
                field="take_profit_price",
            )
        if sl is not None and sl <= fill:
            raise OrderRejected(
                f"A sell's stop-loss must sit above the entry ({sl:.4f} is not above {fill:.4f}).",
                field="stop_loss_price",
            )


def resolve_ticket(
    ticket: OrderTicket,
    *,
    reference_price: float | None,
    equity: float | None,
    buying_power: float | None,
    max_qty: int,
    max_notional: float,
    max_notional_pct: float | None = None,
) -> ResolvedOrder:
    """Price, size and bounds-check a ticket. Never touches the network.

    The entry the order is sized and bounds-checked at is the price it is
    *written* at: the limit for a limit or stop-limit order (the most a buy
    can pay, so the ceilings see the worst case), the trigger for a plain
    stop, the last trade for a market order. `reference_price` is that last
    trade and must come from server-side market data -- a client-supplied
    price must never be able to relax a limit. It also decides whether a
    limit is marketable and whether a stop trigger is on the right side of
    the market; when it is unavailable those checks are skipped rather than
    guessed, since the broker applies its own.
    """
    if ticket.order_type == "market":
        entry = reference_price
    elif ticket.order_type == "stop":
        entry = ticket.stop_price
    else:
        entry = ticket.limit_price
    if entry is None or entry <= 0:
        raise OrderRejected(
            f"No current price available for {ticket.symbol}; cannot size or price the order.",
            field="symbol",
        )

    _check_trigger_side(ticket, reference_price)
    _check_bracket_sides(ticket, _expected_fill(ticket, entry, reference_price))
    warnings = [w for w in (_marketable_warning(ticket, reference_price),) if w]

    sizing: SizingResult | None = None
    risk_pct = None
    if ticket.risk is not None:
        risk_pct = ticket.risk.risk_pct_of_equity
        amount = ticket.risk.risk_amount
        if amount is None:
            if equity is None:
                raise OrderRejected(
                    "Account equity unavailable, so a percentage risk cannot be sized.",
                    field="risk_pct_of_equity",
                )
            amount = risk_amount_for(equity, risk_pct or 0.0)
        sizing = shares_for_risk(
            entry=entry, stop=ticket.risk.stop_price, side=ticket.side, risk_amount=amount
        )
        qty = sizing.qty
    else:
        qty = ticket.qty or 0

    notional = qty * entry
    # The tighter of the absolute backstop and the equity-scaled ceiling.
    # Both exist so neither has to be right on its own.
    ceiling = max_notional
    if max_notional_pct is not None and equity:
        ceiling = min(ceiling, equity * max_notional_pct / 100.0)
    assert_within_limits(
        qty=qty,
        notional=notional,
        buying_power=buying_power,
        max_qty=max_qty,
        max_notional=ceiling,
    )

    # Sizing from a stop implies that stop is the protective exit, so adopt it
    # as the stop-loss leg unless one was given explicitly. Without this a
    # risk-sized order would compute a quantity from a stop it never places.
    stop_loss = ticket.stop_loss_price
    if stop_loss is None and ticket.risk is not None:
        stop_loss = ticket.risk.stop_price

    return ResolvedOrder(
        symbol=ticket.symbol.upper(),
        side=ticket.side,
        order_type=ticket.order_type,
        time_in_force=ticket.time_in_force,
        order_class="bracket"
        if (ticket.take_profit_price is not None and stop_loss is not None)
        else ("oto" if (ticket.take_profit_price is not None or stop_loss is not None) else "simple"),
        qty=qty,
        entry_reference=entry,
        notional=notional,
        limit_price=ticket.limit_price,
        stop_price=ticket.stop_price,
        take_profit_price=ticket.take_profit_price,
        stop_loss_price=stop_loss,
        risk_amount=sizing.risk_amount if sizing else None,
        risk_per_share=sizing.risk_per_share if sizing else None,
        risk_pct_of_equity=risk_pct,
        client_order_id=ticket.client_order_id,
        warnings=warnings,
    )
