"""Position sizing from a stop. Pure arithmetic, no SDK imports.

Same shape as app.scanners.formulas: no I/O, no settings object, no Alpaca
types, so every branch is reachable from a unit test without a network or a
running app.

The whole file exists to answer one question -- "how many shares risks this
much if I'm wrong?" -- and the answer is a division, which is exactly what
makes it dangerous. A stop a tenth of a cent from entry turns $200 of
intended risk into 200,000 shares. Every guard below is there because the
arithmetic is otherwise perfectly happy to produce that.
"""

import math
from dataclasses import dataclass

from app.trading.errors import OrderRejected

# A stop closer than a penny is a typo, a stale quote, or a misplaced decimal
# -- it is not a trade someone meant to make. Rejecting outright is better
# than sizing it, because the resulting quantity is absurd by construction
# and any downstream cap would have to reject it anyway, later and less
# clearly.
MIN_RISK_PER_SHARE = 0.01
# Prices arrive as decimal-ish floats, so a stop the user set exactly one
# penny away computes as 0.009999999999999787 and would be rejected on the
# wrong side of the boundary. Compare with a tolerance far smaller than any
# real tick so the boundary itself is inclusive.
_FLOAT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class SizingResult:
    qty: int
    risk_per_share: float
    risk_amount: float
    notional: float


def risk_amount_for(equity: float, risk_pct: float) -> float:
    """Dollars to risk, from a percentage of account equity."""
    if equity <= 0:
        raise OrderRejected("Account equity is zero or unavailable.", field="risk_pct")
    if risk_pct <= 0:
        raise OrderRejected("Risk percentage must be greater than zero.", field="risk_pct")
    return equity * risk_pct / 100.0


def shares_for_risk(
    *,
    entry: float,
    stop: float,
    side: str,
    risk_amount: float,
) -> SizingResult:
    """Whole shares whose loss at `stop` is at most `risk_amount`.

    Raises OrderRejected rather than returning a sentinel: every failure here
    is something the user must see and fix, and a None would otherwise have
    to be re-interpreted into a message by each caller.
    """
    if entry <= 0:
        raise OrderRejected("Entry price must be greater than zero.", field="entry")
    if stop <= 0:
        raise OrderRejected("Stop price must be greater than zero.", field="stop_price")
    if risk_amount <= 0:
        raise OrderRejected("Risk amount must be greater than zero.", field="risk_amount")

    # A stop on the wrong side is never a typo worth silently correcting: for
    # a buy it would mean the position is already beyond its exit, and
    # flipping it would place a different trade than the one described.
    if side == "buy" and stop >= entry:
        raise OrderRejected(
            f"A buy's stop must sit below the entry ({stop:.4f} is not below {entry:.4f}).",
            field="stop_price",
        )
    if side == "sell" and stop <= entry:
        raise OrderRejected(
            f"A sell's stop must sit above the entry ({stop:.4f} is not above {entry:.4f}).",
            field="stop_price",
        )

    risk_per_share = abs(entry - stop)
    if risk_per_share < MIN_RISK_PER_SHARE - _FLOAT_TOLERANCE:
        raise OrderRejected(
            f"Stop is {risk_per_share:.4f} from entry -- closer than the "
            f"{MIN_RISK_PER_SHARE:.2f} minimum. That distance sizes to an "
            "implausible quantity; widen the stop.",
            field="stop_price",
        )

    # Floor, never round: rounding up would risk more than asked, which is
    # the one direction this must never fail in.
    qty = math.floor(risk_amount / risk_per_share)
    if qty < 1:
        needed = risk_per_share
        raise OrderRejected(
            f"Risking {risk_amount:.2f} at {risk_per_share:.4f} per share sizes to "
            f"less than one share. At least {needed:.2f} is needed for one.",
            field="risk_amount",
        )

    return SizingResult(
        qty=qty,
        risk_per_share=risk_per_share,
        risk_amount=qty * risk_per_share,
        notional=qty * entry,
    )


def assert_within_limits(
    *,
    qty: int,
    notional: float,
    buying_power: float | None,
    max_qty: int,
    max_notional: float,
) -> None:
    """Pre-flight ceilings, checked before anything reaches the broker.

    Deliberately redundant with the stop-distance guard above: they catch the
    same class of failure by different means, so removing one still leaves
    the damage bounded. Buying power is checked here too rather than left to
    Alpaca, because a local refusal can name the number.
    """
    if qty > max_qty:
        raise OrderRejected(
            f"{qty:,} shares exceeds the {max_qty:,} share ceiling.", field="qty"
        )
    if notional > max_notional:
        # Say what to change. A risk-sized order's notional is
        # risk x (entry / stop-distance), so a tight stop inflates position
        # size even when the risk itself is small -- which is not obvious
        # from the number alone.
        raise OrderRejected(
            f"{notional:,.2f} exceeds the {max_notional:,.2f} order ceiling. "
            "A tighter stop means a bigger position for the same risk, so widen "
            "the stop, lower the risk %, or raise TRADING_MAX_ORDER_NOTIONAL_PCT.",
            field="qty",
        )
    if buying_power is not None and notional > buying_power:
        # Rejected, not clamped. A silently reduced size is not the trade
        # whose risk the user just calculated.
        affordable = int(buying_power // (notional / qty)) if qty else 0
        raise OrderRejected(
            f"{notional:,.2f} exceeds buying power of {buying_power:,.2f} "
            f"({affordable:,} shares affordable).",
            field="qty",
        )
