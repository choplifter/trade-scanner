"""Spread arithmetic. Pure: no SDK, no settings, no network.

Prices here are per spread and per share -- the way a chain quotes them.
Money (max profit, max loss, collateral) is per share x 100 x spreads,
which is where the contract multiplier lives, and nowhere else.
"""

from dataclasses import dataclass
from typing import Literal

from app.trading.errors import OrderRejected

CONTRACT_MULTIPLIER = 100

Direction = Literal["debit", "credit"]


@dataclass(frozen=True)
class SpreadRisk:
    direction: Direction
    width: float
    max_profit: float
    max_loss: float
    breakevens: list[float]
    # What the broker holds against the position: the debit paid, or the
    # width less the credit received.
    collateral: float


def net_price(legs, use: Literal["mid", "natural"] = "mid") -> float | None:
    """The package's net price per spread, signed like Alpaca's MLEG limit:
    positive is a debit (you pay), negative a credit (you receive).

    "mid" prices every leg at its midpoint; "natural" is the price that
    crosses the market -- buy legs at the ask, sell legs at the bid -- i.e.
    the worst case a marketable order can fill at. None if any leg lacks
    the quote needed, because a package price with a hole in it is not a
    price.
    """
    total = 0.0
    for leg in legs:
        ratio = getattr(leg, "ratio_qty", 1) or 1
        if use == "mid":
            price = leg.mid
        else:
            price = leg.ask if leg.side == "buy" else leg.bid
        if price is None:
            return None
        total += (price if leg.side == "buy" else -price) * ratio
    return round(total, 4)


def spread_risk(strategy: str, strikes: tuple[float, ...], price: float, qty: int) -> SpreadRisk:
    """Max profit / max loss / breakevens / collateral for a defined-risk
    spread at a given positive net price per spread. `strikes` are in the
    ticket's canonical order (see SpreadTicket.strikes)."""
    if price <= 0:
        raise OrderRejected("The net price must be positive", field="limit_price")
    money = CONTRACT_MULTIPLIER * qty

    if strategy == "iron_condor":
        put_long, put_short, call_short, call_long = strikes
        width = max(put_short - put_long, call_long - call_short)
        if price >= width:
            raise OrderRejected(
                f"Credit {price:.2f} is not below the wider wing ({width:g}) -- check the legs",
                field="limit_price",
            )
        return SpreadRisk(
            direction="credit",
            width=round(width, 4),
            max_profit=round(price * money, 2),
            max_loss=round((width - price) * money, 2),
            breakevens=[round(put_short - price, 2), round(call_short + price, 2)],
            collateral=round((width - price) * money, 2),
        )

    long, short = strikes
    width = abs(short - long)
    if price >= width:
        raise OrderRejected(
            f"Net price {price:.2f} is not below the spread width ({width:g}) -- check the legs",
            field="limit_price",
        )
    if strategy in ("bull_call", "bear_put"):
        breakeven = long + price if strategy == "bull_call" else long - price
        return SpreadRisk(
            direction="debit",
            width=round(width, 4),
            max_profit=round((width - price) * money, 2),
            max_loss=round(price * money, 2),
            breakevens=[round(breakeven, 2)],
            collateral=round(price * money, 2),
        )
    if strategy in ("bull_put", "bear_call"):
        breakeven = short - price if strategy == "bull_put" else short + price
        return SpreadRisk(
            direction="credit",
            width=round(width, 4),
            max_profit=round(price * money, 2),
            max_loss=round((width - price) * money, 2),
            breakevens=[round(breakeven, 2)],
            collateral=round((width - price) * money, 2),
        )
    raise OrderRejected(f"Unknown strategy: {strategy}", field="strategy")


def alpaca_limit(direction: Direction, price: float) -> float:
    """Alpaca's MLEG convention: a positive limit is the most to pay, a
    negative one the least to receive."""
    rounded = round(abs(price), 2)
    return rounded if direction == "debit" else -rounded


def marketable_close_limit(
    direction: Direction, net_mid: float, net_natural: float | None, slippage: float
) -> float:
    """A closing limit meant to fill rather than rest: the mid stepped
    `slippage` toward the natural price and capped there. For a close that
    *receives* (credit) that means asking a little less; for one that
    *pays* (debit), offering a little more. Both arguments are positive
    magnitudes per spread."""
    slip = max(0.0, slippage)
    if direction == "credit":
        price = net_mid - slip
        if net_natural is not None:
            price = max(price, net_natural)
    else:
        price = net_mid + slip
        if net_natural is not None:
            price = min(price, net_natural)
    return max(0.01, round(price, 2))


def assert_spread_within_limits(
    *,
    qty: int,
    collateral: float,
    options_buying_power: float | None,
    max_contracts: int,
    max_notional: float,
) -> None:
    """Pre-flight ceilings, same posture as sizing.assert_within_limits: a
    mistyped quantity is what these exist for, and refusing here gives a
    clearer message than the broker would."""
    if qty > max_contracts:
        raise OrderRejected(
            f"{qty} spreads is over the per-order ceiling of {max_contracts}", field="qty"
        )
    if collateral > max_notional:
        raise OrderRejected(
            f"Max loss / collateral {collateral:,.2f} is over the order ceiling of {max_notional:,.2f}",
            field="qty",
        )
    if options_buying_power is not None and collateral > options_buying_power:
        raise OrderRejected(
            f"Collateral {collateral:,.2f} exceeds options buying power {options_buying_power:,.2f}",
            field="qty",
        )
