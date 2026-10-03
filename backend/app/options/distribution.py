"""The terminal distribution an option chain implies, skew included.

Every chance of profit used to rest on one number: the at-the-money IV,
spread into a symmetric lognormal. The market does not price that. Out of
the money, puts trade at a higher IV than calls (LQD on 2026-10-02: 12 %
at the money, 17 % eight points below it), which says the downside is
fatter than the lognormal makes it -- so a put spread's chance of profit
read off the ATM IV is too generous, by more the further out it sits.

The chain itself carries the distribution it prices. A call's price
falls with its strike, and how fast it falls is the probability of
finishing above that strike (Breeden-Litzenberger); with a smile sigma(K),
no rates and no dividends, as everywhere else in the app:

    P(S_T > K) = N(d2) - S * phi(d1) * sqrt(T) * dsigma/dK

The first term is the lognormal at that strike's own IV; the second is
the smile's slope, which is what moves probability out into the tail.

Steps, each guarded because a chain is noisy and thin at the edges:

1. smile_points: one IV per strike, from the out-of-the-money side (puts
   below spot, calls above) -- the contracts the market prices a view in.
   The two sides are first put on one footing: a feed that solves both
   against the spot rather than the forward pushes them apart by a near
   constant (LQD: calls 7.9 %, puts 11.6 % at the same strike), and the
   mean of the two is what the mids say (9.7 %, checked by hand).
2. fit_smile: a weighted quadratic in log-moneyness over the strikes near
   the money, held flat beyond the last quoted strike. Smooth on purpose:
   the formula reads the smile's slope, and a slope taken between two
   noisy strikes is mostly noise.
3. Distribution: the CDF on a fine grid, clamped to [0, 1] and forced
   monotone (a noisy fit can imply a sliver of negative density), with
   lognormal tails beyond it.

When the chain does not carry enough to fit -- too few strikes, or none on
one side -- there is no smile, and the caller falls back to the ATM
lognormal it always used. Absent is absent: no skew is invented.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

# How far either side of the money the smile is fitted, in ATM standard
# deviations of the move -- and at least this much log-moneyness, so a
# short-dated chain still reaches a few strikes.
FIT_REACH_SIGMAS = 3.0
MIN_FIT_REACH = 0.03
# Strikes either side of spot that count as "near the money" for the
# call/put offset (see smile_points).
OFFSET_BAND = 0.03
# The CDF grid: points, and reach in ATM standard deviations.
CDF_POINTS = 801
CDF_REACH_SIGMAS = 6.0
MIN_SIGMA = 0.01


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _side(row, kind: str):
    """A strike row's quote for one side, from a Chain's StrikeRow or from
    the Optimizer's condensed dict rows -- both carry .iv/.bid or ['iv']/['bid']."""
    quote = row.get(kind) if isinstance(row, dict) else getattr(row, kind, None)
    if quote is None:
        return None, None
    if isinstance(quote, dict):
        return quote.get("iv"), quote.get("bid")
    return getattr(quote, "iv", None), getattr(quote, "bid", None)


def _strike(row) -> float:
    return float(row["strike"] if isinstance(row, dict) else row.strike)


def smile_points(rows, spot: float) -> list[tuple[float, float]]:
    """(strike, IV) from the out-of-the-money side of each strike, both
    sides shifted half the near-the-money call/put gap toward each other.
    Only quotes with an IV and a bid: a contract nobody bids for has an IV
    the feed solved from nothing."""
    if spot <= 0:
        return []
    sides = []
    for row in rows:
        call_iv, call_bid = _side(row, "call")
        put_iv, put_bid = _side(row, "put")
        call = call_iv if call_iv and call_iv > 0 and call_bid and call_bid > 0 else None
        put = put_iv if put_iv and put_iv > 0 and put_bid and put_bid > 0 else None
        sides.append((_strike(row), call, put))
    gaps = [c - p for k, c, p in sides if c and p and abs(k / spot - 1) <= OFFSET_BAND]
    offset = sum(gaps) / len(gaps) if gaps else 0.0
    points = []
    for strike, call, put in sides:
        call = None if call is None else call - offset / 2
        put = None if put is None else put + offset / 2
        if strike < spot:
            iv = put if put is not None else call
        elif strike > spot:
            iv = call if call is not None else put
        else:
            both = [v for v in (call, put) if v is not None]
            iv = sum(both) / len(both) if both else None
        if iv is not None and iv > 0:
            points.append((strike, iv))
    return points


@dataclass(frozen=True)
class Smile:
    """sigma(k) = a + b k + c k^2 on [k_lo, k_hi] (k = log-moneyness),
    continued in a straight line beyond it at the slope it has at the edge.

    Not flat beyond the edge. The CDF reads the smile's slope directly, and
    a slope that drops to zero at the last quoted strike makes the CDF jump
    there -- the wrong way on the put wing, a stretch of negative density
    (measured: P(S_T < K) stuck at 4.6 % from 75 to 80 on a 30 % smile at
    45 days). Continuing the slope keeps it continuous. Over the reach the
    CDF is read on (+/- 6 ATM standard deviations) a straight wing stays
    well inside what a smile can do."""

    a: float
    b: float
    c: float
    k_lo: float
    k_hi: float

    def _raw(self, k: float) -> float:
        if k < self.k_lo:
            return self._quad(self.k_lo) + self._dquad(self.k_lo) * (k - self.k_lo)
        if k > self.k_hi:
            return self._quad(self.k_hi) + self._dquad(self.k_hi) * (k - self.k_hi)
        return self._quad(k)

    def _quad(self, k: float) -> float:
        return self.a + self.b * k + self.c * k * k

    def _dquad(self, k: float) -> float:
        return self.b + 2 * self.c * k

    def sigma(self, k: float) -> float:
        return max(MIN_SIGMA, self._raw(k))

    def slope(self, k: float) -> float:
        """d sigma / d k; zero only where the floor holds."""
        if self._raw(k) <= MIN_SIGMA:
            return 0.0
        return self._dquad(min(max(k, self.k_lo), self.k_hi))


def _solve3(m: list[list[float]], v: list[float]) -> list[float] | None:
    """3x3 linear solve by Cramer's rule; None when singular."""

    def det(x):
        return (
            x[0][0] * (x[1][1] * x[2][2] - x[1][2] * x[2][1])
            - x[0][1] * (x[1][0] * x[2][2] - x[1][2] * x[2][0])
            + x[0][2] * (x[1][0] * x[2][1] - x[1][1] * x[2][0])
        )

    d = det(m)
    if abs(d) < 1e-18:
        return None
    out = []
    for i in range(3):
        mi = [row[:] for row in m]
        for r in range(3):
            mi[r][i] = v[r]
        out.append(det(mi) / d)
    return out


def fit_smile(points: list[tuple[float, float]], spot: float, atm_sigma: float, years: float) -> Smile | None:
    """A weighted least-squares quadratic in log-moneyness through the
    strikes near the money, weighted toward the money (where the quotes are
    tightest). A concave fit -- implausible for an equity smile, and what
    two noisy wing quotes produce -- is refitted as a straight line. None
    with fewer than three strikes in reach or none on one side of spot."""
    if spot <= 0 or atm_sigma <= 0 or years <= 0:
        return None
    width = atm_sigma * math.sqrt(years)
    reach = max(FIT_REACH_SIGMAS * width, MIN_FIT_REACH)
    data = [(math.log(k / spot), iv) for k, iv in points if k > 0 and abs(math.log(k / spot)) <= reach]
    if len(data) < 3 or not any(k < 0 for k, _ in data) or not any(k > 0 for k, _ in data):
        return None
    weights = [math.exp(-0.5 * (k / width) ** 2) + 0.05 for k, _ in data]

    def solve(quadratic: bool):
        n = 3 if quadratic else 2
        m = [[sum(w * k ** (i + j) for (k, _), w in zip(data, weights)) for j in range(n)] for i in range(n)]
        v = [sum(w * iv * k**i for (k, iv), w in zip(data, weights)) for i in range(n)]
        if n == 3:
            return _solve3(m, v)
        d = m[0][0] * m[1][1] - m[0][1] * m[1][0]
        if abs(d) < 1e-18:
            return None
        return [(v[0] * m[1][1] - m[0][1] * v[1]) / d, (m[0][0] * v[1] - m[1][0] * v[0]) / d, 0.0]

    coef = solve(True)
    if coef is None or coef[2] < 0:
        coef = solve(False)
    if coef is None:
        return None
    a, b, c = coef
    k_lo, k_hi = min(k for k, _ in data), max(k for k, _ in data)
    return Smile(a=a, b=b, c=c, k_lo=k_lo, k_hi=k_hi)


class Distribution:
    """P(S_T <= price) under the chain's smile, or the ATM lognormal when
    there is none. Built once per expiry and horizon, read many times."""

    def __init__(self, spot: float, atm_sigma: float, years: float, smile: Smile | None = None):
        self.spot = spot
        self.width = atm_sigma * math.sqrt(years)
        self.years = years
        self.smile = smile
        self._xs: list[float] = []
        self._fs: list[float] = []
        if smile is not None:
            self._build()

    @classmethod
    def from_chain(cls, rows, spot: float, atm_sigma: float, years: float) -> "Distribution":
        smile = fit_smile(smile_points(rows, spot), spot, atm_sigma, years)
        return cls(spot, atm_sigma, years, smile)

    @property
    def skewed(self) -> bool:
        return self.smile is not None

    def _lognormal(self, x: float) -> float:
        return _norm_cdf((x + 0.5 * self.width * self.width) / self.width)

    def _build(self) -> None:
        reach = CDF_REACH_SIGMAS * self.width
        step = 2 * reach / (CDF_POINTS - 1)
        root_t = math.sqrt(self.years)
        running = 0.0
        for i in range(CDF_POINTS):
            k = -reach + i * step
            sigma = self.smile.sigma(k)
            sd = sigma * root_t
            d1 = (-k + 0.5 * sd * sd) / sd
            d2 = d1 - sd
            # dsigma/dK = (dsigma/dk) / K, and S / K = exp(-k).
            above = _norm_cdf(d2) - math.exp(-k) * _norm_pdf(d1) * root_t * self.smile.slope(k)
            running = max(running, min(1.0, max(0.0, 1.0 - above)))
            self._xs.append(k)
            self._fs.append(running)

    def cdf(self, price: float) -> float:
        if price <= 0:
            return 0.0
        x = math.log(price / self.spot)
        if not self._xs:
            return self._lognormal(x)
        x0, xn = self._xs[0], self._xs[-1]
        if x <= x0:
            base = self._lognormal(x0)
            return self._fs[0] * (self._lognormal(x) / base if base > 0 else 0.0)
        if x >= xn:
            base = 1.0 - self._lognormal(xn)
            return 1.0 - (1.0 - self._fs[-1]) * ((1.0 - self._lognormal(x)) / base if base > 0 else 0.0)
        i = bisect.bisect_right(self._xs, x)
        xa, xb = self._xs[i - 1], self._xs[i]
        fa, fb = self._fs[i - 1], self._fs[i]
        return fa + (fb - fa) * (x - xa) / (xb - xa)
