"""The risk chart's numbers: a position's P&L over a grid of underlying
prices, at expiry and -- with each leg's implied volatility -- today.

Pure: no SDK, no settings. Black-Scholes with r = 0 and the leg's own IV
(no skew model, no dividends); the "today" curve is therefore an estimate
in the same sense the chain's greeks are. The "at expiry" curve is exact
for single-expiry positions; for a calendar/diagonal it is drawn at the
short leg's expiry with the long leg still valued by Black-Scholes at
its remaining time, which is the number that matters for that trade.

Money is per position: per share x 100 x qty, so the chart reads in the
same dollars as the ticket's max profit / max loss.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.services.market_clock import ET

CONTRACT_MULTIPLIER = 100
GRID_POINTS = 81
# The grid reaches at least this far either side of spot...
MIN_HALF_RANGE_PCT = 0.02
# ...at least this many expected one-sigma moves to the last expiry...
SIGMA_REACH = 3.0
# ...and past the farthest strike by this fraction of the strikes' span, so
# a tight 0DTE butterfly is not a sliver in the middle of a wide chart.
STRIKE_REACH = 0.6

LegKind = Literal["call", "put", "stock"]


@dataclass(frozen=True)
class PayoffLeg:
    kind: LegKind
    strike: float  # a stock leg's strike is its reference price
    side: Literal["buy", "sell"]
    ratio: int = 1
    expiry: date | None = None  # None for a stock leg
    iv: float | None = None

    @property
    def sign(self) -> int:
        return 1 if self.side == "buy" else -1


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(kind: str, spot: float, strike: float, years: float, sigma: float, rate: float = 0.0) -> float:
    """Black-Scholes price of a European call/put; intrinsic when there is
    no time or no volatility left."""
    if years <= 0 or sigma <= 0 or spot <= 0:
        return intrinsic(kind, spot, strike)
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    discount = math.exp(-rate * years)
    if kind == "call":
        return spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
    return strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def intrinsic(kind: str, spot: float, strike: float) -> float:
    if kind == "call":
        return max(spot - strike, 0.0)
    if kind == "put":
        return max(strike - spot, 0.0)
    return spot - strike  # stock: P&L per share against its reference price


# Below this much time to expiry an implied volatility is not solvable in
# any meaningful sense (a 0DTE contract at 15:59 is all intrinsic).
MIN_YEARS = 15 / (365 * 24 * 60)
# A price this close to intrinsic carries no time value to solve for.
_IV_PRICE_FLOOR = 0.005


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def implied_vol(
    kind: str,
    price: float | None,
    spot: float,
    strike: float,
    years: float,
    *,
    lo: float = 0.01,
    hi: float = 5.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """The Black-Scholes volatility that reprices `price` (per share), by
    bisection on bs_price -- what the replayed chain uses in place of the
    IV Alpaca's snapshots carry live (option bars have none). None when the
    price is at or below intrinsic, there is no time left, or no
    volatility in [lo, hi] reaches the price (a stale print against a
    moved spot)."""
    if price is None or price <= 0 or spot <= 0 or strike <= 0 or years <= MIN_YEARS:
        return None
    if price <= intrinsic(kind, spot, strike) + _IV_PRICE_FLOOR:
        return None
    if bs_price(kind, spot, strike, years, lo) - price > 0:
        return None
    if bs_price(kind, spot, strike, years, hi) - price < 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        diff = bs_price(kind, spot, strike, years, mid) - price
        if abs(diff) < tol or (hi - lo) < tol:
            return round(mid, 6)
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 6)


def bs_greeks(
    kind: str, spot: float, strike: float, years: float, sigma: float, rate: float = 0.0
) -> tuple[float, float, float]:
    """(delta, gamma, theta per day) of a European call/put -- the same
    three greeks the chain shows from Alpaca's snapshot, computed here for
    a replayed contract from its solved IV. Theta is per calendar day and
    negative for a long option, like Alpaca's."""
    if years <= 0 or sigma <= 0 or spot <= 0:
        if kind == "call":
            delta = 1.0 if spot > strike else 0.0
        else:
            delta = -1.0 if spot < strike else 0.0
        return delta, 0.0, 0.0
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf = _norm_pdf(d1)
    gamma = pdf / (spot * sigma * sqrt_t)
    theta_year = -(spot * pdf * sigma) / (2.0 * sqrt_t)
    discount = math.exp(-rate * years)
    if kind == "call":
        delta = _norm_cdf(d1)
        theta_year -= rate * strike * discount * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta_year += rate * strike * discount * _norm_cdf(-d2)
    return round(delta, 4), round(gamma, 6), round(theta_year / 365.0, 4)


def _expiry_moment(expiry: date) -> datetime:
    return datetime.combine(expiry, time(16, 0), tzinfo=ET)


def years_between(now: datetime, expiry: date) -> float:
    delta = _expiry_moment(expiry) - now.astimezone(ET)
    return max(0.0, delta / timedelta(days=365))


def leg_value(leg: PayoffLeg, spot: float, at: datetime) -> float | None:
    """A leg's value per share at moment `at` for underlying `spot`: intrinsic
    once expired, Black-Scholes before -- None when that needs an IV the
    leg does not have."""
    if leg.kind == "stock":
        return intrinsic("stock", spot, leg.strike)
    years = years_between(at, leg.expiry) if leg.expiry is not None else 0.0
    if years <= 0:
        return intrinsic(leg.kind, spot, leg.strike)
    if leg.iv is None or leg.iv <= 0:
        return None
    return bs_price(leg.kind, spot, leg.strike, years, leg.iv)


def _position_value(legs: list[PayoffLeg], spot: float, at: datetime) -> float | None:
    total = 0.0
    for leg in legs:
        value = leg_value(leg, spot, at)
        if value is None:
            return None
        total += leg.sign * leg.ratio * value
    return total


def _breakevens(prices: list[float], pnl: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(prices)):
        a, b = pnl[i - 1], pnl[i]
        if a == 0:
            out.append(prices[i - 1])
        elif (a < 0 < b) or (b < 0 < a):
            out.append(prices[i - 1] + (prices[i] - prices[i - 1]) * (-a) / (b - a))
    if pnl and pnl[-1] == 0:
        out.append(prices[-1])
    return [round(p, 2) for p in out]


def _net_calls(legs: list[PayoffLeg]) -> int:
    return sum(leg.sign * leg.ratio for leg in legs if leg.kind == "call") + sum(
        leg.sign * leg.ratio for leg in legs if leg.kind == "stock"
    )


def payoff_curve(
    legs: list[PayoffLeg],
    qty: int,
    net_price: float,
    spot: float,
    now: datetime,
    *,
    points: int = GRID_POINTS,
) -> dict:
    """`net_price` is per share and signed: positive was (or will be) paid,
    negative received. Returns prices, at_expiry, today (None if a leg lacks
    IV), breakevens, max_profit/max_loss (None = unbounded on the upside/
    downside beyond the grid), spot, expiry (the evaluation date of the
    at-expiry curve) and the multiplier."""
    option_legs = [leg for leg in legs if leg.kind != "stock" and leg.expiry is not None]
    if not option_legs:
        raise ValueError("a payoff needs at least one option leg")
    first_expiry = min(leg.expiry for leg in option_legs)  # type: ignore[type-var]
    last_expiry = max(leg.expiry for leg in option_legs)  # type: ignore[type-var]
    at_expiry_moment = _expiry_moment(first_expiry)

    ivs = [leg.iv for leg in option_legs if leg.iv]
    sigma = sum(ivs) / len(ivs) if ivs else 0.0
    reach = SIGMA_REACH * spot * sigma * math.sqrt(years_between(now, last_expiry)) if sigma else 0.0
    strikes = [leg.strike for leg in option_legs]
    span = max(max(strikes) - min(strikes), 0.01 * spot)
    strike_reach = max(abs(k - spot) for k in strikes) + STRIKE_REACH * span
    half = max(MIN_HALF_RANGE_PCT * spot, reach, strike_reach)
    lo, hi = max(0.01, spot - half), spot + half
    prices = [round(lo + (hi - lo) * i / (points - 1), 4) for i in range(points)]
    # The strikes themselves join the grid: the at-expiry curve kinks there,
    # and a breakeven interpolated across a kink would land off by a few
    # cents.
    for leg in option_legs:
        if lo < leg.strike < hi:
            prices.append(round(leg.strike, 4))
    prices = sorted(set(prices))

    multiplier = CONTRACT_MULTIPLIER * qty
    at_expiry: list[float] = []
    today: list[float] | None = []
    for price in prices:
        value = _position_value(legs, price, at_expiry_moment)
        # A long leg beyond the first expiry without IV: value it at intrinsic
        # rather than dropping the curve -- the chart still shows the shape.
        if value is None:
            value = sum(
                leg.sign * leg.ratio * (leg_value(leg, price, at_expiry_moment) or intrinsic(leg.kind, price, leg.strike))
                for leg in legs
            )
        at_expiry.append(round((value - net_price) * multiplier, 2))
        if today is not None:
            now_value = _position_value(legs, price, now)
            if now_value is None:
                today = None
            else:
                today.append(round((now_value - net_price) * multiplier, 2))

    net_calls = _net_calls(legs)
    max_profit: float | None = max(at_expiry)
    max_loss: float | None = min(at_expiry)
    if net_calls > 0:
        max_profit = None  # keeps rising with the underlying
    if net_calls < 0:
        max_loss = None  # keeps falling with the underlying
    return {
        "prices": prices,
        "at_expiry": at_expiry,
        "today": today,
        "breakevens": _breakevens(prices, at_expiry),
        "max_profit": max_profit,
        "max_loss": max_loss,
        "spot": spot,
        "expiry": first_expiry,
        "multiplier": multiplier,
    }
