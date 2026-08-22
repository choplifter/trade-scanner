"""Turning a signal into a trade: where it gets out, and what that was worth.

Every backtest in this app has measured entry-to-session-close. That answers
"did price drift up afterwards", which is not the same question as "would
this trade have made money" -- a signal whose average move is positive still
loses if the path there goes through a stop, and one whose average is flat
can pay if the losses are cut shorter than the wins.

So an outcome here is reported in **R**: multiples of the distance to the
stop, which is the amount actually at risk. R is what makes two trades on
different symbols at different prices comparable, and it is the unit position
sizing already speaks (see app.trading.sizing.shares_for_risk, which sizes
from exactly this distance).

Four things are configurable because real rules differ on them, and each one
changes the result enough that defaulting it silently would measure a
different strategy than the one described:

  * **side** -- long or short. Everything is expressed against the trade's own
    direction, so an R is an R either way. This is the one module in the app
    that is not long-only: the momentum alarm's confirmation (green candle,
    above VWAP) genuinely has no sign-flip, but a stop and a target do, and a
    method that reads a level for rejection in either direction needs both.
  * **stop_trigger** -- whether the stop fires when price *touches* the level
    or only when a bar *closes* through it. A method that enters as close to
    a reference line as possible (VWAP, say) lives or dies on this: a touch
    stop a few cents away is hit by noise almost immediately, so scoring a
    close-based rule as a touch-based one does not produce a conservative
    number, it produces a wrong one.
  * **scale_out** -- taking part of the position off at the target and running
    the rest with the stop moved to break-even. It caps the upside on the
    portion taken and floors the downside on the portion kept, which reshapes
    the R distribution rather than merely shifting it.
  * **cost_bps** -- spread and commission, charged on both sides.

Pure and network-free, so the rule can be tested against hand-built bars.
"""

from dataclasses import dataclass, field

# A 5-minute bar that touches both levels tells us the order of neither. The
# assumption has to be made somewhere, and it is made here, once, in the
# pessimistic direction: a bar spanning both is scored as a stop. The
# alternative flatters every result, and by exactly the amount that the most
# volatile setups -- the ones already measured as the worst -- are flattered
# most.
#
# This applies to touch stops only. A close-triggered stop and a target are
# different order types, not a race: the target is a resting limit that fills
# the moment price trades there, while the stop is only consulted once the bar
# has closed. Whatever order the two happened in, a trade whose target printed
# is already out before the close is read -- so with STOP_ON_CLOSE the target
# wins, and nothing has been assumed.
_AMBIGUOUS_BAR_IS_A_STOP = True

EXIT_STOP = "stop"
EXIT_TARGET = "target"
EXIT_CLOSE = "close"
# The runner after a scale-out, stopped where the trade was opened. Counted
# separately from EXIT_STOP because it is not a loss -- reporting the two
# together would read as a far worse stop rate than the rule actually has.
EXIT_BREAKEVEN = "breakeven"

SIDE_LONG = "long"
SIDE_SHORT = "short"

STOP_ON_TOUCH = "touch"
STOP_ON_CLOSE = "close"


@dataclass(frozen=True)
class ExitRule:
    """Stop and target, both as a fraction of the entry price.

    reward_ratio is the target expressed in R, so 2.0 means "risk one, aim
    for two". cost_bps is charged twice, once entering and once leaving --
    it stands in for spread and commission, which is an *assumption* rather
    than a measurement: historical quotes are not fetched anywhere in this
    app (see backtest.BACKTESTABLE_FIELDS on why spread cannot be
    backtested), so the number has to be supplied rather than observed.

    scale_out is the fraction taken off at the target; the remainder runs on
    with its stop at the entry price until that stop or the session close
    ends it. 0.0 means the whole position leaves at the target.
    """

    stop_pct: float
    reward_ratio: float = 2.0
    cost_bps: float = 0.0
    side: str = SIDE_LONG
    stop_trigger: str = STOP_ON_TOUCH
    scale_out: float = 0.0

    def __post_init__(self) -> None:
        if self.stop_pct <= 0:
            raise ValueError("stop_pct must be positive -- it is the denominator of R.")
        if self.reward_ratio <= 0:
            raise ValueError("reward_ratio must be positive.")
        if self.side not in (SIDE_LONG, SIDE_SHORT):
            raise ValueError(f"side must be {SIDE_LONG!r} or {SIDE_SHORT!r}.")
        if self.stop_trigger not in (STOP_ON_TOUCH, STOP_ON_CLOSE):
            raise ValueError(f"stop_trigger must be {STOP_ON_TOUCH!r} or {STOP_ON_CLOSE!r}.")
        if not 0.0 <= self.scale_out < 1.0:
            # 1.0 would mean "take everything off at the target", which is
            # what 0.0 already does -- allowing both spellings would put two
            # names on one behaviour and invite a report to distinguish them.
            raise ValueError("scale_out must be at least 0 and below 1.")

    @property
    def sign(self) -> int:
        """+1 long, -1 short. Every comparison below is written against this,
        so the two sides share one implementation rather than two that can
        disagree about a gap or an ambiguous bar."""
        return 1 if self.side == SIDE_LONG else -1


@dataclass(frozen=True)
class ExitLeg:
    """One portion of the position leaving."""

    fraction: float
    price: float
    reason: str
    r_multiple: float
    ambiguous: bool = False


@dataclass(frozen=True)
class Exit:
    price: float
    reason: str
    r_multiple: float
    # True when any part of the exit came off a bar that touched both levels,
    # i.e. one resolved by _AMBIGUOUS_BAR_IS_A_STOP rather than by the tape.
    # Reported so the assumption's weight is visible instead of assumed small:
    # it was judged acceptable against 2% stops, and a structural stop can sit
    # far tighter, where the same bar spans both levels far more often.
    ambiguous: bool = False
    # Every portion, in the order it left. One leg unless the rule scales out.
    legs: tuple[ExitLeg, ...] = field(default_factory=tuple)


def simulate_exit(entry_price: float, forward_bars: list, rule: ExitRule) -> Exit:
    """Walk the bars after entry and return where the trade actually ended.

    `forward_bars` must be the bars *after* the entry bar, in order, and must
    stop at the session close -- holding across the close would score an exit
    against an after-hours print, which is the artifact the rest of this
    module's callers already guard against.

    Gaps are filled at the open, not at the level. A stop does not fill at
    the stop price when the next bar opens through it, and pretending
    otherwise quietly removes the worst outcomes from the sample -- which are
    exactly the ones a stop exists to bound. The same applies in the trader's
    favour on the target side, so the treatment is symmetric rather than
    charitable. A close-triggered stop has no gap case at all: it is not in
    the market, so an open beyond it is simply a bar in progress and the fill
    is the close it is eventually read at. That is why such a stop can lose
    several R on one bar, and the backtest should show that rather than
    smooth it away.
    """
    risk_per_share = entry_price * rule.stop_pct
    if not forward_bars:
        # Entered on the last bar of the session: there is nowhere to exit,
        # so the trade never happened rather than closing at its own entry.
        return _compose(entry_price, risk_per_share, rule, [(1.0, entry_price, EXIT_CLOSE, False)])

    stop = entry_price * (1 - rule.sign * rule.stop_pct)
    target = entry_price * (1 + rule.sign * rule.stop_pct * rule.reward_ratio)

    price, reason, ambiguous, index = _walk(forward_bars, 0, stop, target, rule)

    if reason != EXIT_TARGET or rule.scale_out == 0.0:
        return _compose(entry_price, risk_per_share, rule, [(1.0, price, reason, ambiguous)])

    # Part comes off at the target and the rest runs on with its stop at the
    # entry -- "moves the stop to break-even". The runner has no target of its
    # own: the rule that produced it says to hold until a level, and no level
    # is modelled here, so it runs to the session close. That is an
    # approximation in the runner's favour, and a report should say so.
    legs = [(rule.scale_out, price, EXIT_TARGET, ambiguous)]
    runner = 1.0 - rule.scale_out

    # The same bar is checked for the break-even stop before moving on. Price
    # that printed the target and also traded back through the entry inside
    # one bar tells us nothing about the order, so the pessimistic reading
    # applies here exactly as it does above.
    bar = forward_bars[index]
    if _adverse(bar, rule) * rule.sign <= entry_price * rule.sign:
        legs.append((runner, entry_price, EXIT_BREAKEVEN, True))
    else:
        r_price, r_reason, r_ambiguous, _ = _walk(forward_bars, index + 1, entry_price, None, rule)
        if r_reason == EXIT_STOP:
            r_reason = EXIT_BREAKEVEN
        legs.append((runner, r_price, r_reason, r_ambiguous))

    return _compose(entry_price, risk_per_share, rule, legs)


def _adverse(bar, rule: ExitRule) -> float:
    """The extreme that hurts: the low of a long, the high of a short."""
    return bar.low if rule.side == SIDE_LONG else bar.high


def _favourable(bar, rule: ExitRule) -> float:
    return bar.high if rule.side == SIDE_LONG else bar.low


def _walk(bars: list, start: int, stop: float, target: float | None, rule: ExitRule):
    """Find where a position already open leaves, starting at bars[start].

    Returns (price, reason, ambiguous, index). `target` may be None for a
    runner that has none left. Everything is compared through rule.sign, so
    "through the stop" means below it for a long and above it for a short
    without either case being written out twice.
    """
    sign = rule.sign
    for i in range(start, len(bars)):
        bar = bars[i]

        target_touched = target is not None and _favourable(bar, rule) * sign >= target * sign
        gapped_target = target is not None and bar.open * sign >= target * sign
        target_fill = bar.open if gapped_target else target

        if rule.stop_trigger == STOP_ON_CLOSE:
            # A resting limit fills the moment price trades there; a close
            # stop is not read until the bar is over. So a bar doing both
            # leaves the trade at the target either way -- nothing has been
            # assumed, which is why no ambiguity is recorded here.
            if target_touched:
                return target_fill, EXIT_TARGET, False, i
            if bar.close * sign <= stop * sign:
                return bar.close, EXIT_STOP, False, i
            continue

        gapped_stop = bar.open * sign <= stop * sign
        stop_touched = _adverse(bar, rule) * sign <= stop * sign

        # An open already through a level resolves the order for free: the
        # trade was out before the bar had a range to argue about.
        if gapped_stop:
            return bar.open, EXIT_STOP, False, i
        if gapped_target:
            return bar.open, EXIT_TARGET, False, i

        if stop_touched and target_touched:
            if _AMBIGUOUS_BAR_IS_A_STOP:
                return stop, EXIT_STOP, True, i
            return target, EXIT_TARGET, True, i
        if stop_touched:
            return stop, EXIT_STOP, False, i
        if target_touched:
            return target_fill, EXIT_TARGET, False, i

    return bars[-1].close, EXIT_CLOSE, False, len(bars) - 1


def _compose(entry_price: float, risk_per_share: float, rule: ExitRule, legs: list) -> Exit:
    """Blend the legs into one outcome.

    Costs are charged per leg as a full round trip. Each leg shares the one
    entry but pays its own exit, which sums to the same thing -- so a scaled
    exit is not quietly cheaper per share than an unscaled one.
    """
    cost = entry_price * rule.cost_bps / 10_000 * 2
    built = []
    for fraction, price, reason, ambiguous in legs:
        r = (rule.sign * (price - entry_price) - cost) / risk_per_share
        built.append(
            ExitLeg(
                fraction=fraction,
                price=price,
                reason=reason,
                r_multiple=round(r, 4),
                ambiguous=ambiguous,
            )
        )

    blended = sum(leg.fraction * leg.r_multiple for leg in built)
    # The last leg names the outcome: it is where the trade finally ended,
    # and for the common single-leg case it is the only leg there is.
    final = built[-1]
    return Exit(
        price=final.price,
        reason=final.reason,
        r_multiple=round(blended, 4),
        ambiguous=any(leg.ambiguous for leg in built),
        legs=tuple(built),
    )


def expectancy(exits: list[Exit]) -> dict:
    """What the rule was worth, per trade, in R.

    Win rate is reported alongside but deliberately not first: it is the
    number that looks like an edge and is not one. A rule taking +0.3R wins
    and -1R losses can win 70% of the time and still lose money, and one
    winning 40% at +3R is profitable -- expectancy is what separates them.
    """
    if not exits:
        return {
            "trades": 0,
            "expectancy_r": None,
            "win_rate": None,
            "avg_win_r": None,
            "avg_loss_r": None,
            "exits": {},
            "ambiguous_exits": 0,
        }

    rs = [e.r_multiple for e in exits]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    reasons: dict[str, int] = {}
    for exit_ in exits:
        reasons[exit_.reason] = reasons.get(exit_.reason, 0) + 1

    return {
        "trades": len(rs),
        "expectancy_r": round(sum(rs) / len(rs), 4),
        "win_rate": round(len(wins) / len(rs) * 100, 1),
        "avg_win_r": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 4) if losses else None,
        "exits": reasons,
        # How many of those exits the tape did not actually decide. A large
        # share means the result rests on _AMBIGUOUS_BAR_IS_A_STOP rather
        # than on the data, and the honest read is "pessimistic by an
        # unmeasured amount" rather than a number.
        "ambiguous_exits": sum(1 for e in exits if e.ambiguous),
    }
