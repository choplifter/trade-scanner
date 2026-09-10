"""Is implied vol expensive right now, and what is the market pricing in?

The question every options structure turns on before any strike is picked:
sell premium when it is rich, buy it when it is cheap. "Rich" wants an IV
rank -- today's implied vol against its own trailing range -- and that is
the one thing a single snapshot cannot answer, because the range has to be
accumulated (see iv_history_store).

So this module splits the question in two. Everything computable from what
is already on hand is computed here and now:

- **ATM IV per expiry** -- the term structure. A front expiry standing well
  above a back one is an event being priced in, which is exactly what
  decides debit versus credit even with no history at all.
- **IV against realised vol** -- 20-day close-to-close of the underlying,
  annualised, from the daily bars the app already fetches. The ratio is an
  honest "is vol expensive" that needs nothing stored: above 1 the market
  is charging more than the stock has actually been moving.
- **Skew** -- put IV against call IV at comparable distance from spot.

And the part that genuinely needs weeks of history stays absent until it
has them, rather than being faked from the little that is there.

Pure: chains and bar closes in, numbers out. No I/O, no store.
"""

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from app.options.chain import Chain

# Trading days in a year -- the standard annualisation factor, matching the
# convention Alpaca's own IV is quoted in, so the ratio below compares like
# with like.
TRADING_DAYS = 252
REALIZED_VOL_WINDOW = 20
# How far out to look for the strike that stands in for "at the money" when
# spot sits between two listed strikes.
_ATM_TOLERANCE_PCT = 0.02
# How far either side of spot counts as "near the money" when measuring the
# gap between the two sides (see parity_offset).
NEAR_ATM_PCT = 0.03


@dataclass(frozen=True)
class ExpiryIv:
    expiry: date
    dte: int
    atm_iv: float | None
    # Out-of-the-money put IV minus out-of-the-money call IV, the two sides
    # put on one footing first. Positive is the usual equity shape (downside
    # insurance costs more); a call-side bid is unusual enough to be worth
    # naming.
    skew: float | None
    # The gap between the two sides near the money -- an artefact of the
    # feed's forward assumption rather than a view, and what `skew` above
    # has been corrected for. Worth carrying so a reader can see how much
    # of the raw difference was never skew.
    parity_offset: float | None = None

    def to_dict(self) -> dict:
        return {
            "expiry": self.expiry.isoformat(),
            "dte": self.dte,
            "atm_iv": None if self.atm_iv is None else round(self.atm_iv, 4),
            "skew": None if self.skew is None else round(self.skew, 4),
            "parity_offset": None if self.parity_offset is None else round(self.parity_offset, 4),
        }


def _nearest_row(chain: Chain):
    """The strike row closest to spot that quotes an IV on at least one
    side. Not simply the nearest strike: on a wide-striked name the very
    nearest one can be the one Alpaca failed to solve."""
    usable = [
        row
        for row in chain.rows
        if (row.call is not None and row.call.iv) or (row.put is not None and row.put.iv)
    ]
    if not usable:
        return None
    return min(usable, key=lambda row: abs(row.strike - chain.spot))


def atm_iv(chain: Chain) -> float | None:
    """The at-the-money implied vol: the mean of the call and put IV on the
    strike nearest spot, or whichever single side is quoted.

    Averaging the two sides rather than taking the call is deliberate --
    at the money they should agree, and where they do not the mean is the
    less arbitrary of the two answers."""
    row = _nearest_row(chain)
    if row is None:
        return None
    ivs = [q.iv for q in (row.call, row.put) if q is not None and q.iv]
    return sum(ivs) / len(ivs) if ivs else None


def parity_offset(chain: Chain, *, near_pct: float = NEAR_ATM_PCT, min_pairs: int = 2) -> float | None:
    """The systematic gap between the two sides, measured near the money:
    the mean of call IV minus put IV on strikes that quote both.

    Put-call parity says the two sides of one strike carry the same
    implied volatility. Where a feed solves both against the spot price
    rather than the forward, a dividend or a borrow cost pushes them apart
    by roughly a constant -- and on a short-dated contract, where a cent of
    price is worth a lot of volatility, that constant is enormous. Measured
    on ORCL the day before its report: a 27 to 30 point gap on the next
    day's expiry, holding across every strike, and 8 to 11 points a week
    out. That is an artefact of the pricing assumption, not a market view,
    and a skew read straight off the two sides carries all of it.

    None when too few strikes quote both sides to average over."""
    spot = chain.spot
    if spot <= 0:
        return None
    lo, hi = spot * (1 - near_pct), spot * (1 + near_pct)
    gaps = [
        row.call.iv - row.put.iv
        for row in chain.rows
        if lo <= row.strike <= hi and row.call is not None and row.put is not None and row.call.iv and row.put.iv
    ]
    if len(gaps) < min_pairs:
        return None
    return sum(gaps) / len(gaps)


def skew(chain: Chain, *, distance_pct: float = 0.05) -> float | None:
    """How much more the market charges for the downside than the upside,
    in volatility points, at roughly `distance_pct` either side of spot.

    Both legs are out of the money -- the put below spot, the call above --
    because those are the contracts the market actually prices a view in;
    their in-the-money twins are quoted off them and add nothing. Both are
    taken the same distance out, so the number measures asymmetry rather
    than the moneyness of whichever strikes happened to be picked.

    The two sides are put on one footing first (see parity_offset), so what
    is left is the tilt of the curve rather than the feed's forward
    assumption. Without enough strikes to measure that offset the raw
    difference is returned, which is the honest fallback: on a chain that
    thin there is nothing better to correct with."""
    spot = chain.spot
    if spot <= 0:
        return None
    put_target = spot * (1 - distance_pct)
    call_target = spot * (1 + distance_pct)

    puts = [(abs(r.strike - put_target), r.put.iv) for r in chain.rows if r.put is not None and r.put.iv and r.strike <= spot]
    calls = [(abs(r.strike - call_target), r.call.iv) for r in chain.rows if r.call is not None and r.call.iv and r.strike >= spot]
    if not puts or not calls:
        return None
    offset = parity_offset(chain) or 0.0
    return min(puts)[1] - (min(calls)[1] - offset)


def term_structure(chains: Iterable[Chain], today: date) -> list[ExpiryIv]:
    """ATM IV and skew per expiry, nearest first."""
    rows = [
        ExpiryIv(
            expiry=chain.expiry,
            dte=(chain.expiry - today).days,
            atm_iv=atm_iv(chain),
            skew=skew(chain),
            parity_offset=parity_offset(chain),
        )
        for chain in chains
    ]
    return sorted(rows, key=lambda row: row.expiry)


def realized_vol(closes: Sequence[float], *, window: int = REALIZED_VOL_WINDOW) -> float | None:
    """Annualised close-to-close volatility over the last `window` returns.

    Log returns and the sample standard deviation -- the textbook estimator,
    not a bespoke one, so the ratio against implied vol below means what a
    reader expects it to mean. None when there are too few clean closes;
    a short history is not a low volatility."""
    usable = [c for c in closes if c and c > 0]
    if len(usable) < 3:
        return None
    returns = [math.log(b / a) for a, b in zip(usable, usable[1:])]
    returns = returns[-window:]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS)


def iv_premium(implied: float | None, realized: float | None) -> float | None:
    """Implied over realised. Above 1 the market charges more than the stock
    has actually been moving, which is the case for selling premium; below
    1, for buying it. Needs no stored history, which is why it carries the
    weight while an IV rank is still accumulating."""
    if implied is None or realized is None or realized <= 0:
        return None
    return implied / realized
