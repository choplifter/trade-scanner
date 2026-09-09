"""Which option structures fit a symbol's situation -- a rule matrix over
the signals, not a model.

The Optimizer answers "of these shapes, which pays best if the stock is
here on that date". It cannot answer "which shapes are worth trying at
all", because that turns on things its ranking deliberately holds still:
what the market is charging for the event against what the stock has
actually done over past ones, whether implied vol is rich against
realised, how steep the term structure is, where dealers' gamma sits, and
whether the wings a four-legged structure needs are even quoted.

So this scores every family from those signals and hands the Optimizer
the two or three worth pricing. Deterministic and pure: the same signals
give the same answer, and every point carries a sentence naming the
number that produced it, so the reader can disagree with the rule rather
than with a verdict.

Three rules the whole module obeys:

- **None is not evidence.** Every input may be missing, and a missing one
  skips its rule silently -- an absent IV rank is not a low IV rank, and a
  symbol with no GEX reading is not a symbol with neutral positioning.
- **Nothing here is advice.** A score says how many of the available
  signals point at a family and how strongly, relative to the others in
  this list. It is not a probability, not a forecast, and not a
  recommendation.
- **The scores rank, the Optimizer prices.** A family can score well and
  turn out to have no tradable structure behind it; that is the
  Optimizer's answer to give, not this module's to guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.options.models import STRATEGY_LABELS

Group = Literal["neutral", "volatility", "time", "directional"]

# The families this selector scores, by the role they play. Income
# strategies (covered calls, cash-secured puts) are left out: they need
# shares or cash the screen cannot see. Diagonals are left out for the
# same reason the Optimizer leaves them out -- strike x strike x expiry.
FAMILY_GROUPS: dict[str, Group] = {
    "iron_condor": "neutral",
    "iron_butterfly": "neutral",
    "call_butterfly": "neutral",
    "put_butterfly": "neutral",
    "bull_put": "neutral",
    "bear_call": "neutral",
    "long_straddle": "volatility",
    "long_strangle": "volatility",
    "calendar": "time",
    "bull_call": "directional",
    "bear_put": "directional",
    "long_call": "directional",
    "long_put": "directional",
}

# Selling premium into an event and buying it are the two sides of the
# same bet, so they move together under most rules.
_SHORT_PREMIUM = ("iron_condor", "iron_butterfly", "bull_put", "bear_call")
_LONG_VOL = ("long_straddle", "long_strangle")
_DEBIT_VERTICALS = ("bull_call", "bear_put")
_BUTTERFLIES = ("call_butterfly", "put_butterfly")
_LONG_OPTIONS = ("long_call", "long_put")

# Thresholds. Deliberately round numbers: these are the edges of a
# judgement, not measurements, and pretending to two decimals would
# suggest a precision the inputs do not have.
RICH_RATIO = 1.15  # implied move this many times the historical one
CHEAP_RATIO = 0.85
STEEP_TERM = 1.25  # front ATM IV over back
IV_RANK_RICH = 60.0
IV_RANK_CHEAP = 30.0
IV_OVER_REALIZED_RICH = 1.5
IV_OVER_REALIZED_CHEAP = 0.9
SKEW_HEAVY = 0.05  # put IV minus call IV
MIN_WING_STRIKES = 3  # offerable strikes each side beyond the expected move
MAX_PICKS = 3
MAX_PICKS_RED_MARKET = 2
# How much of the typical earnings move a neutral structure is judged
# over. See target_policy for why this is a half and not the whole move.
NEUTRAL_MOVE_FRACTION = 0.5


@dataclass(frozen=True)
class Signals:
    """What the selector reads. Every field but `spot` may be None, and
    None always means "not known"."""

    spot: float
    implied_move_pct: float | None = None
    hist_median_pct: float | None = None
    hist_max_pct: float | None = None
    samples: int = 0
    iv_over_realized: float | None = None
    iv_rank_pct: float | None = None
    front_iv: float | None = None
    back_iv: float | None = None
    skew: float | None = None
    gex_regime: str | None = None
    near_flip: bool | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    offerable_puts_below: int = 0
    offerable_calls_above: int = 0
    wing_room: bool | None = None
    market_level: str | None = None
    reported: bool = False

    @property
    def move_ratio(self) -> float | None:
        """Implied move over the typical past one. Above 1 the market
        charges more for this print than the stock has historically
        delivered; below 1, less. None without both numbers -- and
        without at least two past reports, one blow-up is not a
        history."""
        if self.implied_move_pct is None or not self.hist_median_pct or self.samples < 2:
            return None
        return self.implied_move_pct / self.hist_median_pct

    @property
    def term_slope(self) -> float | None:
        """Front ATM IV over back. Above 1 the near expiry carries the
        event premium the far one does not."""
        if not self.front_iv or not self.back_iv:
            return None
        return self.front_iv / self.back_iv

    def to_dict(self) -> dict:
        return {
            "spot": round(self.spot, 4),
            "implied_move_pct": self.implied_move_pct,
            "hist_median_pct": self.hist_median_pct,
            "hist_max_pct": self.hist_max_pct,
            "samples": self.samples,
            "move_ratio": None if self.move_ratio is None else round(self.move_ratio, 2),
            "iv_over_realized": self.iv_over_realized,
            "iv_rank_pct": self.iv_rank_pct,
            "front_iv": None if self.front_iv is None else round(self.front_iv, 4),
            "back_iv": None if self.back_iv is None else round(self.back_iv, 4),
            "term_slope": None if self.term_slope is None else round(self.term_slope, 2),
            "skew": None if self.skew is None else round(self.skew, 4),
            "gex_regime": self.gex_regime,
            "near_flip": self.near_flip,
            "call_wall": self.call_wall,
            "put_wall": self.put_wall,
            "offerable_puts_below": self.offerable_puts_below,
            "offerable_calls_above": self.offerable_calls_above,
            "wing_room": self.wing_room,
            "market_level": self.market_level,
            "reported": self.reported,
        }


@dataclass
class FamilyScore:
    family: str
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def group(self) -> Group:
        return FAMILY_GROUPS[self.family]

    @property
    def label(self) -> str:
        return STRATEGY_LABELS.get(self.family, self.family)

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "label": self.label,
            "group": self.group,
            "score": self.score,
            "reasons": list(self.reasons),
        }


def score_families(signals: Signals) -> list[FamilyScore]:
    """Every family with its points and the sentences behind them, best
    first. A family with no rule to its name comes back at zero with an
    empty list -- which is "nothing here argues for or against it", not
    "this is a bad idea"."""
    scores = {family: FamilyScore(family=family) for family in FAMILY_GROUPS}

    def add(families, delta: int, reason: str) -> None:
        for family in families:
            entry = scores[family]
            entry.score += delta
            entry.reasons.append(reason)

    ratio = signals.move_ratio
    if ratio is not None:
        stated = (
            f"implied move {signals.implied_move_pct:.1f} % against a median "
            f"{signals.hist_median_pct:.1f} % over {signals.samples} past reports (x{ratio:.2f})"
        )
        if ratio >= RICH_RATIO:
            add(("iron_condor", "iron_butterfly"), 2, f"the market charges more than the stock has moved: {stated}")
            add(("bull_put", "bear_call"), 1, f"premium is rich: {stated}")
            add(("calendar",), 1, f"the event premium sits in the front expiry: {stated}")
            add(_LONG_VOL, -2, f"buying the move looks expensive: {stated}")
        elif ratio <= CHEAP_RATIO:
            add(_LONG_VOL, 2, f"the market charges less than the stock has moved: {stated}")
            add(_DEBIT_VERTICALS, 1, f"premium is cheap: {stated}")
            add(("iron_condor", "iron_butterfly"), -2, f"little to collect for the risk: {stated}")
            add(("bull_put", "bear_call"), -1, f"little to collect for the risk: {stated}")

    slope = signals.term_slope
    if slope is not None and slope >= STEEP_TERM:
        stated = f"front IV {signals.front_iv:.0%} against {signals.back_iv:.0%} further out (x{slope:.2f})"
        add(("calendar",), 2, f"the near expiry carries the event premium: {stated}")
        add(("long_straddle",), -1, f"the leg you would buy is the expensive one: {stated}")
        add(_LONG_OPTIONS, -1, f"the leg you would buy is the expensive one: {stated}")

    if signals.iv_rank_pct is not None:
        stated = f"IV rank {signals.iv_rank_pct:.0f} of its own recorded range"
        if signals.iv_rank_pct >= IV_RANK_RICH:
            add(("iron_condor",), 1, f"implied vol is high for this symbol: {stated}")
            add(("bull_put", "bear_call"), 1, f"implied vol is high for this symbol: {stated}")
        elif signals.iv_rank_pct <= IV_RANK_CHEAP:
            add(_BUTTERFLIES, 1, f"implied vol is low for this symbol: {stated}")
            add(_DEBIT_VERTICALS, 1, f"implied vol is low for this symbol: {stated}")
            add(_LONG_OPTIONS, 1, f"implied vol is low for this symbol: {stated}")
            add(("iron_condor",), -1, f"less premium than usual to sell: {stated}")

    if signals.iv_over_realized is not None:
        stated = f"implied vol {signals.iv_over_realized:.2f}x the 20-day realised"
        if signals.iv_over_realized >= IV_OVER_REALIZED_RICH:
            add(_SHORT_PREMIUM, 1, f"the market charges more than the stock has been moving: {stated}")
        elif signals.iv_over_realized <= IV_OVER_REALIZED_CHEAP:
            add(_LONG_VOL, 1, f"the market charges less than the stock has been moving: {stated}")

    if signals.gex_regime is not None:
        if signals.near_flip:
            add(_LONG_VOL, 1, "spot sits on the gamma flip, where dealer hedging switches from damping to amplifying")
            add(_DEBIT_VERTICALS, 1, "spot sits on the gamma flip")
            add(("iron_condor", "iron_butterfly"), -1, "spot sits on the gamma flip: a tight range is the wrong side of it")
        elif signals.gex_regime == "positive":
            walled = (
                signals.call_wall is not None
                and signals.put_wall is not None
                and signals.put_wall < signals.spot < signals.call_wall
            )
            if walled:
                add(
                    ("iron_condor", "iron_butterfly"),
                    1,
                    f"dealers are net long gamma and spot sits between the put wall at {signals.put_wall:g} "
                    f"and the call wall at {signals.call_wall:g}, which their hedging tends to defend",
                )
        elif signals.gex_regime == "negative":
            add(_LONG_VOL, 1, "dealers are net short gamma, so their hedging tends to amplify whichever way price starts")
            add(_DEBIT_VERTICALS, 1, "dealers are net short gamma")
            add(("iron_condor", "iron_butterfly"), -1, "dealers are net short gamma: moves tend to run rather than mean-revert")

    if signals.skew is not None:
        if signals.skew >= SKEW_HEAVY:
            stated = f"puts carry {signals.skew:.1%} more implied vol than calls the same distance out"
            add(("bull_put",), 1, f"the richer side to sell is the put side: {stated}")
            add(("bear_call",), -1, f"the call side is the cheaper one to sell: {stated}")
        elif signals.skew <= -SKEW_HEAVY:
            stated = f"calls carry {-signals.skew:.1%} more implied vol than puts the same distance out"
            add(("bear_call",), 1, f"the richer side to sell is the call side: {stated}")
            add(("bull_put",), -1, f"the put side is the cheaper one to sell: {stated}")

    if signals.wing_room is False:
        stated = (
            f"only {signals.offerable_puts_below} quotable strikes below and "
            f"{signals.offerable_calls_above} above the expected move"
        )
        add(("iron_condor", "iron_butterfly"), -2, f"there are no wings to buy: {stated}")
        add(("bull_put", "bear_call"), 1, f"a two-legged spread needs less of the chain: {stated}")
        add(_BUTTERFLIES, 1, f"a body-and-wings sits closer to the money: {stated}")

    if signals.market_level == "red":
        add(_SHORT_PREMIUM, -2, "market conditions are red: selling premium into a print on a broadly weak tape")

    ranked = sorted(scores.values(), key=lambda s: (-s.score, s.family))
    return ranked


def warnings_for(signals: Signals) -> list[str]:
    """What the reader should know regardless of which family wins."""
    out: list[str] = []
    if signals.reported:
        out.append(
            "This symbol has already published: the event premium has been paid out, and the numbers below "
            "describe what is left rather than the print itself."
        )
    if signals.market_level == "red":
        out.append("Market conditions are red; short-premium families are marked down and at most two are picked.")
    elif signals.market_level == "yellow":
        out.append("Market conditions are yellow: the tape is mixed, which is not in any of the scores below.")
    if signals.move_ratio is None and signals.implied_move_pct is not None:
        out.append(
            "No usable history of past reports, so the implied move stands alone -- it is not being compared "
            "with anything."
        )
    if signals.iv_rank_pct is None:
        out.append("No IV rank yet for this symbol: not enough recorded sessions. That is unknown, not unremarkable.")
    if signals.gex_regime is None:
        out.append("No gamma-exposure reading for this symbol; positioning is unknown, not neutral.")
    return out


def pick_families(scores: list[FamilyScore], *, market_level: str | None = None, max_picks: int = MAX_PICKS) -> list[str]:
    """The families worth pricing: positive scores from the neutral and
    volatility groups, best first.

    Only those two groups: a directional family needs a view on which way
    the stock goes, which is the reader's to hold and not something any
    of these signals supplies; and a calendar's whole edge is the front
    expiry's implied vol collapsing, which the Optimizer's ranking cannot
    see (it holds IV still). Both are still scored and reported -- they
    just go to the Optimizer tab with a preset rather than being priced
    here."""
    limit = MAX_PICKS_RED_MARKET if market_level == "red" else max_picks
    eligible = [s for s in scores if s.score > 0 and s.group in ("neutral", "volatility")]
    return [s.family for s in eligible[:limit]]


def target_policy(signals: Signals, picks: list[str]) -> dict:
    """The move each group's target should be built from, as a fraction
    of spot.

    The Optimizer ranks a range target by its *worst* point, so whatever
    range a neutral structure is given, it has to earn at both ends of
    it. Judging one over the *whole* typical earnings move turned out to
    reject everything a person could actually trade: only structures
    whose short strikes sit beyond that move survive it, and beyond that
    move a chain is thin, badly quoted, or simply stops. Measured on ORCL
    into its report, all 92 enumerated shapes were dropped at the full
    move, while the same chain at half of it produced six, the best of
    them quoted five cents wide.

    So a neutral structure is judged over half the typical move, and the
    full one travels alongside as context (`full_move`) rather than as a
    hurdle. A volatility structure keeps the larger of the implied and
    the typical move: it needs the move to happen, so the honest question
    is what it pays if one does."""
    hist = (signals.hist_median_pct or 0.0) / 100.0
    implied = (signals.implied_move_pct or 0.0) / 100.0
    full_move = hist or implied
    neutral_move = round(full_move * NEUTRAL_MOVE_FRACTION, 4)
    directional_move = max(hist, implied)
    groups = {FAMILY_GROUPS[f] for f in picks}
    return {
        "neutral": {
            "families": [f for f in picks if FAMILY_GROUPS[f] == "neutral"],
            "move": neutral_move,
            "full_move": round(full_move, 4),
            "basis": f"half the {'median past report move' if hist else 'implied move'}",
        }
        if "neutral" in groups and neutral_move > 0
        else None,
        "volatility": {
            "families": [f for f in picks if FAMILY_GROUPS[f] == "volatility"],
            "move": round(directional_move, 4),
            "basis": "the larger of the implied and the median past move",
        }
        if "volatility" in groups and directional_move > 0
        else None,
    }


__all__ = [
    "FAMILY_GROUPS",
    "FamilyScore",
    "MAX_PICKS",
    "Signals",
    "pick_families",
    "score_families",
    "target_policy",
    "warnings_for",
]
