"""Enumerate, price and rank option structures for a price target -- the
pure half of the Optimizer (app.options.optimize is the orchestration).

Modelled on OptionStrat's optimizer, built on this app's own pieces: the
candidates come from the condensed chain rows the Idea tab already feeds
the model (app.ai.options_resolve.condense_chain -- listed, tradable,
quoted on both sides, not absurdly wide, actually held), their risk from
pricing.spread_risk (the same closed forms the ticket shows), and their
P/L at the target from payoff.leg_value (the same Black-Scholes the risk
chart draws). Nothing here is estimated by a model or a rule of thumb.

What "best" means: the P/L if the underlying sits at the target on the
horizon date with every leg's implied volatility unchanged, divided by
what the account puts up for the position (the debit paid, or the
collateral of a credit structure). A range target uses the *worst* point
of the range -- "wherever in your range it lands, at least this" -- which
is easier to explain than an average and harder to game. It is a return
on risk, not a probability: nothing here says how likely the target is.

Everything that is dropped is counted with its reason (Skipped), because
"no structure reaches this target" and "every structure was over budget"
are different answers and the reader should be able to tell them apart.

No I/O: works on plain dicts and dataclasses so it can be tested with a
hand-written chain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from app.options.models import DEBIT_STRATEGIES, STRATEGY_LABELS, SpreadTicket, TicketLeg
from app.options.payoff import CONTRACT_MULTIPLIER, PayoffLeg, leg_value
from app.options.pricing import spread_risk
from app.trading.errors import OrderRejected
from app.ai.options_resolve import ticket_from_legs

# Bounds on the enumeration. Wide enough to find the shapes a person would
# build by hand, tight enough that a dense SPY chain stays around a thousand
# candidates -- pricing is cheap (dict lookups and a few Black-Scholes
# evaluations), but the count is what the reader gets told, and ten
# thousand near-duplicates would not be a more honest answer.
MAX_CANDIDATES = 1000
FINALISTS = 12
PER_STRATEGY_CAP = 3
VERTICAL_MAX_WIDTH = 3  # strikes between long and short
CONDOR_WING_WIDTHS = (1, 2, 3)
CONDOR_SHORT_DELTA = (0.10, 0.40)  # |delta| band for the short strikes
FLY_WING_WIDTHS = (1, 2, 3, 4)
FLY_BODIES = 3  # strikes nearest the target considered as a body
STRANGLE_WIDTHS = (1, 2, 3)
TARGET_POINTS = 5
# The price grid the chance of profit integrates over: +/- 4 sigma of the
# lognormal move to the horizon, in 201 steps.
CHANCE_GRID_POINTS = 201
CHANCE_SIGMA_REACH = 4.0

# OptionStrat's sentiment buttons, as the strategy families each one
# searches. The target price is the frontend's to set from the implied move
# (+/- one implied move for bearish/bullish, two for the "very" ones, the
# spot for neutral, both sides for directional); this is only which shapes
# are worth trying for that view.
Outlook = Literal["very_bearish", "bearish", "neutral", "directional", "bullish", "very_bullish"]
OUTLOOK_STRATEGIES: dict[str, frozenset[str]] = {
    "very_bearish": frozenset({"long_put", "bear_put"}),
    "bearish": frozenset({"long_put", "bear_put", "bear_call"}),
    "neutral": frozenset({"iron_condor", "iron_butterfly", "call_butterfly", "put_butterfly", "calendar"}),
    "directional": frozenset({"long_straddle", "long_strangle"}),
    "bullish": frozenset({"long_call", "bull_call", "bull_put"}),
    "very_bullish": frozenset({"long_call", "bull_call"}),
}

# Diagonals are left out: strike x strike x expiry pairs multiply the count
# for a shape the ticket can build by hand in a moment. Income strategies are
# off by default because they need shares or cash the optimizer cannot see.
DEFAULT_STRATEGIES: frozenset[str] = frozenset(
    {
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
    }
)

SkipReason = Literal[
    "no_market",
    "no_iv",
    "wrong_way_market",
    "risk_shape",
    "over_budget",
    "over_max_loss",
    "non_positive_return",
    "candidate_cap",
    "strategy_cap",
]


@dataclass(frozen=True)
class Target:
    """Where the reader expects the underlying on the horizon date -- one
    price, a range, or (a directional view) a set of prices on both sides
    of the spot. `points` samples a range so the worst case within it can
    be found without assuming where in the range it lands; explicit points
    are taken as given."""

    low: float
    high: float
    explicit: tuple[float, ...] | None = None

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    @property
    def is_range(self) -> bool:
        return self.explicit is None and self.high > self.low

    def points(self, n: int = TARGET_POINTS) -> list[float]:
        if self.explicit:
            return list(self.explicit)
        if self.high <= self.low or n <= 1:
            return [self.low]
        step = (self.high - self.low) / (n - 1)
        return [round(self.low + i * step, 4) for i in range(n)]


@dataclass(frozen=True)
class RawCandidate:
    """A shape before pricing: strategy, the ticket's expiry and its legs in
    the order SpreadTicket expects (see ticket_from_legs)."""

    strategy: str
    expiry: date
    legs: tuple[TicketLeg, ...]

    @property
    def key(self) -> tuple[str, date]:
        return (self.strategy, self.expiry)


@dataclass
class Candidate:
    strategy: str
    expiry: date
    legs: tuple[TicketLeg, ...]
    # Signed per share like the ticket: positive paid, negative received.
    net_price: float
    direction: Literal["debit", "credit"]
    # What the account puts up, per position: spread_risk's collateral, or
    # its max loss where the collateral is the shares (covered call).
    risk: float
    max_profit: float | None
    max_loss: float | None
    breakevens: list[float]
    pnl_points: list[float]
    # Model probability that the position is profitable on the horizon
    # date -- see chance_of_profit. None when no volatility was available.
    chance: float | None = None

    @property
    def pnl_min(self) -> float:
        return min(self.pnl_points)

    @property
    def pnl_max(self) -> float:
        return max(self.pnl_points)

    @property
    def pnl_mean(self) -> float:
        return sum(self.pnl_points) / len(self.pnl_points)

    @property
    def return_on_risk(self) -> float:
        return self.pnl_min / self.risk if self.risk > 0 else 0.0

    @property
    def label(self) -> str:
        return STRATEGY_LABELS.get(self.strategy, self.strategy)  # type: ignore[arg-type]

    def legs_label(self) -> str:
        return legs_label(self.legs, self.expiry)


@dataclass
class Skipped:
    """What was dropped and why. `total` is every shape enumerated, `scored`
    the ones that priced; the reasons cover both the pricing drops and the
    ranking filters, so total == scored + pricing drops, and the results
    plus the ranking drops == scored."""

    total: int = 0
    scored: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def add(self, reason: str, n: int = 1) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + n

    def to_dict(self) -> dict:
        return {"total": self.total, "scored": self.scored, "reasons": dict(sorted(self.reasons.items()))}


def legs_label(legs: tuple[TicketLeg, ...] | list[TicketLeg], expiry: date) -> str:
    """"+95P −100P", a butterfly body "−2×100C", a calendar's far leg with
    its month and day "+100C 10/16"."""
    parts = []
    for leg in legs:
        sign = "+" if leg.side == "buy" else "−"
        ratio = f"{leg.ratio}×" if leg.ratio > 1 else ""
        strike = f"{leg.strike:g}"
        kind = "C" if leg.kind == "call" else "P"
        far = f" {leg.expiry.month}/{leg.expiry.day}" if leg.expiry is not None and leg.expiry != expiry else ""
        parts.append(f"{sign}{ratio}{strike}{kind}{far}")
    return " ".join(parts)


# --- the chain as the enumerator sees it ----------------------------------------


def _side(row: dict, kind: str) -> dict | None:
    quote = row.get(kind)
    if not quote or quote.get("mid") is None or quote["mid"] <= 0:
        return None
    return quote


def _quoted(rows: list[dict], kind: str) -> list[dict]:
    """Rows with a usable quote on `kind`, ascending by strike."""
    return sorted((r for r in rows if _side(r, kind) is not None), key=lambda r: r["strike"])


def _both(rows: list[dict]) -> list[dict]:
    return sorted((r for r in rows if _side(r, "call") is not None and _side(r, "put") is not None), key=lambda r: r["strike"])


def _index_nearest(rows: list[dict], price: float) -> int | None:
    if not rows:
        return None
    return min(range(len(rows)), key=lambda i: (abs(rows[i]["strike"] - price), rows[i]["strike"]))


def _leg(kind: str, strike: float, side: str, *, ratio: int = 1, expiry: date | None = None) -> TicketLeg:
    return TicketLeg(kind=kind, strike=strike, side=side, ratio=ratio, expiry=expiry)


# --- enumeration -----------------------------------------------------------------


def enumerate_candidates(
    rows_by_expiry: dict[date, list[dict]],
    spot: float,
    target: Target,
    strategies: frozenset[str] = DEFAULT_STRATEGIES,
    *,
    max_candidates: int = MAX_CANDIDATES,
) -> tuple[list[RawCandidate], Skipped]:
    """Every shape worth pricing, per expiry, within the bounds above.

    `rows_by_expiry` holds condensed chain rows per expiry (see
    condense_chain); every expiry in it is assumed to be on or after the
    horizon -- a leg expiring before the horizon has no value to speak of
    there, so the orchestration does not load such expiries. Calendars pair
    each expiry with every later one in the dict.

    Over `max_candidates` the rest is counted under `candidate_cap` rather
    than silently truncated.
    """
    out: list[RawCandidate] = []
    skipped = Skipped()
    expiries = sorted(rows_by_expiry)

    def emit(strategy: str, expiry: date, legs: list[TicketLeg]) -> None:
        skipped.total += 1
        if len(out) >= max_candidates:
            skipped.add("candidate_cap")
            return
        out.append(RawCandidate(strategy, expiry, tuple(legs)))

    for ei, expiry in enumerate(expiries):
        rows = rows_by_expiry[expiry]
        calls = _quoted(rows, "call")
        puts = _quoted(rows, "put")
        both = _both(rows)

        if "long_call" in strategies:
            for r in calls:
                emit("long_call", expiry, [_leg("call", r["strike"], "buy")])
        if "long_put" in strategies:
            for r in puts:
                emit("long_put", expiry, [_leg("put", r["strike"], "buy")])
        if "covered_call" in strategies:
            for r in calls:
                if r["strike"] >= spot:
                    emit("covered_call", expiry, [_leg("call", r["strike"], "sell")])
        if "cash_secured_put" in strategies:
            for r in puts:
                if r["strike"] <= spot:
                    emit("cash_secured_put", expiry, [_leg("put", r["strike"], "sell")])

        # Verticals: every pair of one kind up to VERTICAL_MAX_WIDTH strikes
        # apart, in both orientations. Legs are (long, short), the order
        # ticket_from_legs reads for the strike-field strategies.
        for kind, side_rows, bull, bear in (("call", calls, "bull_call", "bear_call"), ("put", puts, "bull_put", "bear_put")):
            for i in range(len(side_rows)):
                for j in range(i + 1, min(len(side_rows), i + VERTICAL_MAX_WIDTH + 1)):
                    lo, hi = side_rows[i]["strike"], side_rows[j]["strike"]
                    if bull in strategies:
                        emit(bull, expiry, [_leg(kind, lo, "buy"), _leg(kind, hi, "sell")])
                    if bear in strategies:
                        emit(bear, expiry, [_leg(kind, hi, "buy"), _leg(kind, lo, "sell")])

        if "iron_condor" in strategies:
            short_puts = [i for i, r in enumerate(puts) if _short_candidate(r, "put", spot)]
            short_calls = [i for i, r in enumerate(calls) if _short_candidate(r, "call", spot)]
            for pi in short_puts:
                for ci in short_calls:
                    if puts[pi]["strike"] >= calls[ci]["strike"]:
                        continue
                    for w in CONDOR_WING_WIDTHS:
                        if pi - w < 0 or ci + w >= len(calls):
                            continue
                        emit(
                            "iron_condor",
                            expiry,
                            [
                                _leg("put", puts[pi - w]["strike"], "buy"),
                                _leg("put", puts[pi]["strike"], "sell"),
                                _leg("call", calls[ci]["strike"], "sell"),
                                _leg("call", calls[ci + w]["strike"], "buy"),
                            ],
                        )

        for strategy, kind, side_rows in (("call_butterfly", "call", calls), ("put_butterfly", "put", puts)):
            if strategy not in strategies:
                continue
            for bi in _nearest_indices(side_rows, target.mid, FLY_BODIES):
                for w in FLY_WING_WIDTHS:
                    if bi - w < 0 or bi + w >= len(side_rows):
                        continue
                    emit(
                        strategy,
                        expiry,
                        [
                            _leg(kind, side_rows[bi - w]["strike"], "buy"),
                            _leg(kind, side_rows[bi]["strike"], "sell", ratio=2),
                            _leg(kind, side_rows[bi + w]["strike"], "buy"),
                        ],
                    )

        if "iron_butterfly" in strategies:
            for bi in _nearest_indices(both, target.mid, FLY_BODIES):
                body = both[bi]["strike"]
                p_idx = next((i for i, r in enumerate(puts) if r["strike"] == body), None)
                c_idx = next((i for i, r in enumerate(calls) if r["strike"] == body), None)
                if p_idx is None or c_idx is None:
                    continue
                for w in FLY_WING_WIDTHS:
                    if p_idx - w < 0 or c_idx + w >= len(calls):
                        continue
                    emit(
                        "iron_butterfly",
                        expiry,
                        [
                            _leg("put", puts[p_idx - w]["strike"], "buy"),
                            _leg("put", body, "sell"),
                            _leg("call", body, "sell"),
                            _leg("call", calls[c_idx + w]["strike"], "buy"),
                        ],
                    )

        if "long_straddle" in strategies:
            for bi in _nearest_indices(both, target.mid, 3):
                k = both[bi]["strike"]
                emit("long_straddle", expiry, [_leg("put", k, "buy"), _leg("call", k, "buy")])

        if "long_strangle" in strategies:
            ci = _index_nearest(both, target.mid)
            if ci is not None:
                centre = both[ci]["strike"]
                p_at = next((i for i, r in enumerate(puts) if r["strike"] == centre), None)
                c_at = next((i for i, r in enumerate(calls) if r["strike"] == centre), None)
                if p_at is not None and c_at is not None:
                    for w in STRANGLE_WIDTHS:
                        if p_at - w < 0 or c_at + w >= len(calls):
                            continue
                        emit(
                            "long_strangle",
                            expiry,
                            [_leg("put", puts[p_at - w]["strike"], "buy"), _leg("call", calls[c_at + w]["strike"], "buy")],
                        )

        if "calendar" in strategies:
            for far in expiries[ei + 1 :]:
                far_rows = rows_by_expiry[far]
                for kind in ("call", "put"):
                    near_k = _quoted(rows, kind)
                    far_strikes = {r["strike"] for r in _quoted(far_rows, kind)}
                    shared = [r for r in near_k if r["strike"] in far_strikes]
                    for si in _nearest_indices(shared, target.mid, 3):
                        k = shared[si]["strike"]
                        emit(
                            "calendar",
                            expiry,
                            [_leg(kind, k, "sell", expiry=expiry), _leg(kind, k, "buy", expiry=far)],
                        )

    return out, skipped


def _short_candidate(row: dict, kind: str, spot: float) -> bool:
    """A strike a condor might sell: out of the money, and -- when the feed
    gave a delta -- inside the delta band."""
    quote = _side(row, kind)
    if quote is None:
        return False
    if kind == "put" and row["strike"] >= spot:
        return False
    if kind == "call" and row["strike"] <= spot:
        return False
    delta = quote.get("delta")
    if delta is None:
        return True
    lo, hi = CONDOR_SHORT_DELTA
    return lo <= abs(delta) <= hi


def _nearest_indices(rows: list[dict], price: float, n: int) -> list[int]:
    if not rows:
        return []
    order = sorted(range(len(rows)), key=lambda i: (abs(rows[i]["strike"] - price), rows[i]["strike"]))
    return sorted(order[:n])


# --- pricing at the target ---------------------------------------------------------


def _lookup(rows_by_expiry: dict[date, list[dict]], expiry: date, kind: str, strike: float) -> dict | None:
    for row in rows_by_expiry.get(expiry, []):
        if row["strike"] == strike:
            return _side(row, kind)
    return None


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def chance_of_profit(
    legs: list[PayoffLeg],
    net_price: float,
    horizon: datetime,
    spot: float,
    sigma: float,
    years: float,
    qty: int = 1,
) -> float | None:
    """The probability that the position shows a profit on the horizon
    date, under the distribution the option market itself implies: the
    underlying's log-return to the horizon is normal with standard
    deviation sigma*sqrt(T) (sigma the at-the-money implied volatility)
    and no drift beyond the lognormal correction -- the same assumption
    every "chance of profit" figure rests on, OptionStrat's included.
    Integrates the P/L over a price grid +/- 4 sigma and adds up the
    probability mass where it is positive. A model number: it says how
    likely the implied distribution makes a profit, not how likely a
    profit is."""
    if sigma <= 0 or years <= 0 or spot <= 0:
        return None
    width = sigma * math.sqrt(years)
    mu = -0.5 * width * width
    lo = -CHANCE_SIGMA_REACH * width
    hi = CHANCE_SIGMA_REACH * width
    n = CHANCE_GRID_POINTS
    step = (hi - lo) / (n - 1)
    total = 0.0
    for i in range(n):
        x = lo + i * step
        # Probability mass of this step of log-return.
        a = _norm_cdf((x - step / 2 - mu) / width)
        b = _norm_cdf((x + step / 2 - mu) / width)
        mass = b - a
        if mass <= 0:
            continue
        price = spot * math.exp(x)
        pnl = position_pnl(legs, net_price, price, horizon, qty)
        if pnl is None:
            return None
        if pnl > 0:
            total += mass
    return round(min(1.0, max(0.0, total)), 4)


def position_pnl(legs: list[PayoffLeg], net_price: float, price: float, at: datetime, qty: int = 1) -> float | None:
    """P/L per position at `price` on `at`, the risk chart's own arithmetic
    (payoff_curve): every leg valued by Black-Scholes at its IV, intrinsic
    once expired, minus what was paid, times the multiplier. None when a
    leg with time left has no IV to value it with."""
    total = 0.0
    for leg in legs:
        value = leg_value(leg, price, at)
        if value is None:
            return None
        total += leg.sign * leg.ratio * value
    return round((total - net_price) * CONTRACT_MULTIPLIER * qty, 2)


def price_candidate(
    raw: RawCandidate,
    rows_by_expiry: dict[date, list[dict]],
    spot: float,
    target: Target,
    horizon: datetime,
    *,
    qty: int = 1,
    sigma: float | None = None,
    years: float | None = None,
) -> Candidate | str:
    """A priced candidate, or the reason it cannot be one. With `sigma`
    (the at-the-money IV) and `years` to the horizon the candidate also
    carries its chance of profit."""
    mids: list[float] = []
    payoff_legs: list[PayoffLeg] = []
    for leg in raw.legs:
        expiry = leg.expiry or raw.expiry
        quote = _lookup(rows_by_expiry, expiry, leg.kind, leg.strike)
        if quote is None:
            return "no_market"
        mids.append(quote["mid"])
        payoff_legs.append(
            PayoffLeg(kind=leg.kind, strike=leg.strike, side=leg.side, ratio=leg.ratio, expiry=expiry, iv=quote.get("iv"))
        )
    signed = sum((1 if leg.side == "buy" else -1) * leg.ratio * mid for leg, mid in zip(raw.legs, mids))
    direction: Literal["debit", "credit"] = "debit" if raw.strategy in DEBIT_STRATEGIES else "credit"
    if (direction == "debit" and signed <= 0) or (direction == "credit" and signed >= 0):
        return "wrong_way_market"
    price = round(abs(signed), 2)
    if price <= 0:
        return "no_market"

    try:
        risk = spread_risk(raw.strategy, tuple(leg.strike for leg in raw.legs), price, qty, stock_price=spot)
    except OrderRejected:
        return "risk_shape"
    denominator = risk.collateral if risk.collateral > 0 else risk.max_loss
    if denominator <= 0:
        return "risk_shape"

    if raw.strategy == "covered_call":
        # The shares behind the call, at what they are worth now -- as
        # service._payoff draws it.
        payoff_legs.append(PayoffLeg(kind="stock", strike=spot, side="buy"))
    pnl_points: list[float] = []
    for point in target.points():
        pnl = position_pnl(payoff_legs, signed, point, horizon, qty)
        if pnl is None:
            return "no_iv"
        pnl_points.append(pnl)

    chance = None
    if sigma is not None and years is not None and years > 0:
        chance = chance_of_profit(payoff_legs, signed, horizon, spot, sigma, years, qty)

    return Candidate(
        strategy=raw.strategy,
        expiry=raw.expiry,
        legs=raw.legs,
        net_price=round(signed, 4),
        direction=direction,
        risk=denominator,
        max_profit=risk.max_profit,
        max_loss=risk.max_loss,
        breakevens=risk.breakevens,
        pnl_points=pnl_points,
        chance=chance,
    )


# --- ranking ----------------------------------------------------------------------


def rank_score(candidates: list[Candidate], preference: float) -> dict[int, float]:
    """One score per candidate (by id) blending two rankings: return on
    risk and chance of profit, each as a percentile within the list, mixed
    by `preference` -- 0 is all return, 1 is all chance, the slider in
    between. Percentiles rather than raw values so one absurd return does
    not flatten every chance into irrelevance. Without chances the score
    is the return percentile alone."""
    n = len(candidates)
    if n == 0:
        return {}
    by_ror = sorted(candidates, key=lambda c: c.return_on_risk)
    ror_rank = {id(c): i / max(1, n - 1) for i, c in enumerate(by_ror)}
    with_chance = [c for c in candidates if c.chance is not None]
    if not with_chance:
        return {id(c): ror_rank[id(c)] for c in candidates}
    by_chance = sorted(with_chance, key=lambda c: c.chance)  # type: ignore[arg-type,return-value]
    m = len(with_chance)
    chance_rank = {id(c): i / max(1, m - 1) for i, c in enumerate(by_chance)}
    p = min(1.0, max(0.0, preference))
    return {id(c): (1 - p) * ror_rank[id(c)] + p * chance_rank.get(id(c), 0.0) for c in candidates}


def filter_and_rank(
    candidates: list[Candidate],
    *,
    budget: float | None = None,
    max_loss: float | None = None,
    top_k: int = FINALISTS,
    per_strategy_cap: int = PER_STRATEGY_CAP,
    preference: float = 0.0,
) -> tuple[list[Candidate], dict[str, int]]:
    """The best `top_k`, with the drop reasons counted.

    `budget` caps what the account puts up per position; `max_loss` caps
    the defined maximum loss (an unbounded one never passes it). A shape
    that loses money at the worst point of the target is out: the reader
    asked what pays off there. The order blends return on risk and chance
    of profit by `preference` (see rank_score) -- 0 is the slider at Max
    Return, 1 at Max Chance. At most `per_strategy_cap` per strategy and
    expiry, so a list is not twelve bull calls one strike apart.
    """
    reasons: dict[str, int] = {}

    def drop(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    kept: list[Candidate] = []
    for cand in candidates:
        if budget is not None and cand.risk > budget:
            drop("over_budget")
            continue
        if max_loss is not None and (cand.max_loss is None or cand.max_loss > max_loss):
            drop("over_max_loss")
            continue
        if cand.pnl_min <= 0:
            drop("non_positive_return")
            continue
        kept.append(cand)

    scores = rank_score(kept, preference)
    kept.sort(key=lambda c: (-scores[id(c)], -c.return_on_risk, -c.pnl_mean, c.risk))
    out: list[Candidate] = []
    seen: dict[tuple[str, date], int] = {}
    for cand in kept:
        key = (cand.strategy, cand.expiry)
        if seen.get(key, 0) >= per_strategy_cap:
            drop("strategy_cap")
            continue
        seen[key] = seen.get(key, 0) + 1
        out.append(cand)
        if len(out) >= top_k:
            break
    return out, reasons


def candidate_ticket(underlying: str, cand: Candidate | RawCandidate, qty: int = 1) -> SpreadTicket:
    """The ticket the widget loads -- the same builder the Idea tab uses, so
    SpreadTicket's own validation is the last word on the shape."""
    return ticket_from_legs(underlying, cand.strategy, cand.expiry, list(cand.legs), qty)  # type: ignore[arg-type]


__all__ = [
    "Candidate",
    "OUTLOOK_STRATEGIES",
    "chance_of_profit",
    "rank_score",
    "DEFAULT_STRATEGIES",
    "FINALISTS",
    "MAX_CANDIDATES",
    "PER_STRATEGY_CAP",
    "RawCandidate",
    "Skipped",
    "Target",
    "candidate_ticket",
    "enumerate_candidates",
    "filter_and_rank",
    "legs_label",
    "position_pnl",
    "price_candidate",
]
