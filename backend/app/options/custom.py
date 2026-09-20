"""What a freely built position is worth knowing before it is placed.

The named shapes get their risk from closed-form arithmetic keyed on the
name (pricing.spread_risk): a condor's ceiling is its width less the
credit, a butterfly's is a wing. The builder has no name to key on -- any
one to four legs, any sides, ratios and expiries -- so the numbers come
from the payoff curve instead, and the margin of a short leg nothing
covers comes from the broker's own formula.

Probabilities live here too, for the same reason: the optimizer computes
the chance of profit for its candidates (optimizer.chance_of_profit) and
the builder wants the same number, plus the one a seller actually asks --
whether the market gets to the breakeven at all before expiry.
"""

import math
from dataclasses import dataclass

from app.options.models import SpreadLeg
from app.options.payoff import _norm_cdf

CONTRACT_MULTIPLIER = 100

# Reg-T's requirement for an uncovered option, the one US brokers apply:
# a fifth of the underlying less what the strike is out of the money, and
# never below a tenth (of the underlying for a call, of the strike for a
# put), plus the premium taken in. Alpaca's own number can differ; this is
# an estimate and is labelled as one wherever it is shown.
_NAKED_FRACTION = 0.20
_NAKED_FLOOR = 0.10


@dataclass
class CustomRisk:
    max_profit: float | None
    max_loss: float | None
    breakevens: list[float]
    collateral: float
    width: float
    # True when a short leg has nothing covering it: the collateral above
    # is the broker's estimated margin, not a ceiling on the loss.
    naked: bool


def naked_margin(kind: str, strike: float, spot: float, premium: float) -> float:
    """Reg-T margin for one uncovered contract, per contract."""
    out_of_the_money = max(0.0, strike - spot) if kind == "call" else max(0.0, spot - strike)
    base = _NAKED_FRACTION * spot - out_of_the_money
    floor = _NAKED_FLOOR * (spot if kind == "call" else strike)
    return round((max(base, floor) + max(0.0, premium)) * CONTRACT_MULTIPLIER, 2)


def custom_risk(
    legs: list[SpreadLeg],
    bare: list[SpreadLeg],
    qty: int,
    spot: float,
    max_profit: float | None,
    max_loss: float | None,
    breakevens: list[float],
) -> CustomRisk:
    """The ceilings from the payoff curve, and what the position ties up.

    Defined risk ties up what it can lose. A position with a short leg
    nothing covers has no such number -- the payoff curve reports an
    unbounded side as None -- so the broker's margin stands in, summed per
    bare leg."""
    strikes = [leg.strike for leg in legs]
    width = round(max(strikes) - min(strikes), 4) if strikes else 0.0
    if bare:
        collateral = 0.0
        for leg in bare:
            premium = leg.mid or 0.0
            collateral += naked_margin(leg.kind, leg.strike, spot, premium) * qty * (leg.ratio_qty or 1)
        return CustomRisk(max_profit, max_loss, breakevens, round(collateral, 2), width, True)
    collateral = round(abs(max_loss), 2) if max_loss is not None else 0.0
    return CustomRisk(max_profit, max_loss, breakevens, collateral, width, False)


def chance_of_touch(spot: float, barrier: float, sigma: float, years: float) -> float | None:
    """The probability that the underlying trades at `barrier` at any point
    before the horizon, under the same driftless lognormal the chance of
    profit uses: 2 * N(-|ln(B/S)| / (sigma*sqrt(T))), the reflection
    principle's answer for a single barrier.

    Why it is worth showing beside the chance of profit: a credit spread
    can be 80 % likely to expire worthless and still be 40 % likely to have
    the market at its short strike in between, which is when the position
    is actually managed -- or closed in a panic."""
    if sigma <= 0 or years <= 0 or spot <= 0 or barrier <= 0:
        return None
    width = sigma * math.sqrt(years)
    if width <= 0:
        return None
    distance = abs(math.log(barrier / spot))
    return min(1.0, round(2.0 * _norm_cdf(-distance / width), 4))


def nearest_breakeven(breakevens: list[float], spot: float) -> float | None:
    """The one the market reaches first, which is the one worth a number."""
    if not breakevens:
        return None
    return min(breakevens, key=lambda b: abs(b - spot))
