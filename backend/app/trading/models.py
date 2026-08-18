"""The order ticket, and everything that must be true before it is sent.

Validation is split in two on purpose. Pydantic handles what can be judged
from the ticket alone -- a limit order needs a limit price, exactly one
sizing mode, bracket legs on the correct side of entry. `resolve_ticket`
handles what needs the account and settings: the computed quantity, the
ceilings, buying power.

order_class is derived here and never accepted from the client, so a
malformed combination of legs is not expressible over the wire.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.trading.errors import OrderRejected
from app.trading.sizing import SizingResult, assert_within_limits, risk_amount_for, shares_for_risk

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]


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
    time_in_force: Literal["day", "gtc"] = "day"

    # Exactly one of qty / risk, for the same reason as above.
    qty: int | None = Field(default=None, gt=0)
    risk: RiskSizing | None = None

    limit_price: float | None = Field(default=None, gt=0)
    take_profit_price: float | None = Field(default=None, gt=0)
    stop_loss_price: float | None = Field(default=None, gt=0)

    # Client-generated so a retry after a timeout is the *same* order rather
    # than a second one -- Alpaca rejects a duplicate id. Minting it
    # server-side would defeat that, since a retry would arrive with a new one.
    client_order_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def check_shape(self) -> "OrderTicket":
        if (self.qty is None) == (self.risk is None):
            raise ValueError("Provide exactly one of qty or risk.")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("A limit order needs a limit_price.")
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("A market order cannot carry a limit_price.")
        return self

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
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    risk_amount: float | None = None
    risk_per_share: float | None = None
    risk_pct_of_equity: float | None = None
    client_order_id: str | None = None


def _check_bracket_sides(ticket: OrderTicket, entry: float) -> None:
    """Take-profit above and stop below for a buy; inverted for a sell.

    Alpaca rejects these too, but late and with an opaque message. Refusing
    here names the leg that is wrong.
    """
    tp, sl = ticket.take_profit_price, ticket.stop_loss_price
    if ticket.side == "buy":
        if tp is not None and tp <= entry:
            raise OrderRejected(
                f"A buy's take-profit must sit above the entry ({tp:.4f} is not above {entry:.4f}).",
                field="take_profit_price",
            )
        if sl is not None and sl >= entry:
            raise OrderRejected(
                f"A buy's stop-loss must sit below the entry ({sl:.4f} is not below {entry:.4f}).",
                field="stop_loss_price",
            )
    else:
        if tp is not None and tp >= entry:
            raise OrderRejected(
                f"A sell's take-profit must sit below the entry ({tp:.4f} is not below {entry:.4f}).",
                field="take_profit_price",
            )
        if sl is not None and sl <= entry:
            raise OrderRejected(
                f"A sell's stop-loss must sit above the entry ({sl:.4f} is not above {entry:.4f}).",
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
) -> ResolvedOrder:
    """Price, size and bounds-check a ticket. Never touches the network.

    `reference_price` is the limit price for a limit order and the last trade
    for a market order. It is used for sizing and for every ceiling, so it
    must come from server-side market data -- a client-supplied price must
    never be able to relax a limit.
    """
    entry = ticket.limit_price if ticket.order_type == "limit" else reference_price
    if entry is None or entry <= 0:
        raise OrderRejected(
            f"No current price available for {ticket.symbol}; cannot size or price the order.",
            field="symbol",
        )

    _check_bracket_sides(ticket, entry)

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
    assert_within_limits(
        qty=qty,
        notional=notional,
        buying_power=buying_power,
        max_qty=max_qty,
        max_notional=max_notional,
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
        take_profit_price=ticket.take_profit_price,
        stop_loss_price=stop_loss,
        risk_amount=sizing.risk_amount if sizing else None,
        risk_per_share=sizing.risk_per_share if sizing else None,
        risk_pct_of_equity=risk_pct,
        client_order_id=ticket.client_order_id,
    )
