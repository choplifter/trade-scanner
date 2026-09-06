"""What happens to a simulated contract at its expiry -- the pure decision,
separate from the book that carries it out.

A real account at 16:00 ET on expiry day: an out-of-the-money contract
expires worthless; an in-the-money short put is assigned (the writer buys
100 shares per contract at the strike); an in-the-money short call is
assigned too, and the shares behind a covered call are called away at the
strike. Everything else here is settled in cash at intrinsic value, which
is what a broker's automatic exercise-and-liquidate comes to:

  * a long in-the-money option is exercised by the OCC regardless, but
    exercising a call needs strike x 100 in cash and exercising a put needs
    the shares, which a simulated account may not have; the broker would
    liquidate the result at once, so shares would appear and vanish at the
    same price. The cash result is identical and the stock book stays clean;
  * an in-the-money short call with no shares behind it is a naked
    assignment -- short shares -- which this book does not carry; its
    intrinsic value is charged instead, the same loss.

On assignment the option leg closes at 0.00 rather than at intrinsic: the
economic loss is carried by the shares (bought at the strike, marked at the
spot), so a zero-priced close makes the option's round trip equal the
premium kept -- what a statement shows -- and avoids counting the intrinsic
once in the option trade and again in the shares' unrealized P&L. It also
gives the shares a cost basis of exactly the strike, which is the number a
wheel is reasoned about with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.options.payoff import intrinsic

SHARES_PER_CONTRACT = 100

Outcome = Literal["expired", "cash_settled", "assigned", "called_away"]


@dataclass(frozen=True)
class Settlement:
    """One outcome for `contracts` of a held position.

    option_price: what the option leg closes at (0.0 for expired, assigned and
    called_away; the intrinsic value for cash_settled).
    share_side / share_qty / share_price: the stock fill that goes with it
    (assigned: buy at the strike; called_away: sell at the strike), or none.
    """

    outcome: Outcome
    contracts: int
    option_price: float
    share_side: Literal["buy", "sell"] | None = None
    share_qty: int = 0
    share_price: float | None = None

    @property
    def moves_shares(self) -> bool:
        return self.share_qty > 0


def decide_settlement(
    *,
    side: str,
    kind: str,
    strike: float,
    spot: float,
    contracts: int,
    shares_long: int,
) -> list[Settlement]:
    """The settlements for a position of `contracts` (positive) held `side`
    ("long" | "short") in a `kind` ("call" | "put") at `strike`, with the
    underlying at `spot` and `shares_long` shares of it held long.

    One entry in the usual case; two for a short call that is only partly
    covered (the covered contracts are called away, the rest cash-settled).
    """
    if contracts <= 0:
        return []
    value = round(intrinsic(kind, float(spot), float(strike)), 4)
    if value <= 0:
        return [Settlement("expired", contracts, 0.0)]
    if side == "short" and kind == "put":
        return [
            Settlement(
                "assigned",
                contracts,
                0.0,
                share_side="buy",
                share_qty=SHARES_PER_CONTRACT * contracts,
                share_price=float(strike),
            )
        ]
    if side == "short" and kind == "call":
        covered = min(contracts, max(shares_long, 0) // SHARES_PER_CONTRACT)
        out: list[Settlement] = []
        if covered > 0:
            out.append(
                Settlement(
                    "called_away",
                    covered,
                    0.0,
                    share_side="sell",
                    share_qty=SHARES_PER_CONTRACT * covered,
                    share_price=float(strike),
                )
            )
        if contracts - covered > 0:
            out.append(Settlement("cash_settled", contracts - covered, value))
        return out
    return [Settlement("cash_settled", contracts, value)]
