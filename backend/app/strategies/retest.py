"""The shared part of a break-and-retest: everything except which line.

Two rules use it -- VWAP and the opening-range boundary -- and they differ
in the line that was broken and in what "clearly beyond" it means there.
Everything after that is identical: what counts as a hold, what counts as
the return to the line, where the stop sits, how far the target must be,
how the position is managed. One implementation for the same reason
strategies.breakout is one: two files that look alike are the state that
rots, and comparing the rules is the reason both exist.

The grammar, stated once: a close is a verdict, a wick is a visit. The
break is a close clearly beyond the line; a hold is a close that stayed
beyond it; the retest is a wick that came back while the body stayed out --
and a close back through the line is never a test, it is the break failing,
whether it happens behind the retest (invalidating it) or on the retest bar
itself.

The stop sits at the retest bar's own wick -- the print the market just
defended -- rather than a constructed distance past the line. Asked for by
the user after the first measurement: a stop measured off the line put the
trade cents from its stop, 1R was a fraction of a percent, and cost ate it.
The wick anchors risk to something the stock actually did.

Infrastructure, not a strategy: listed in loader._EXCLUDED, and the
loader's own test asserts that every non-strategy file here is.
"""

from app.scanners.exit_rules import SIDE_LONG, STOP_ON_CLOSE
from app.strategies.context import Signal, next_level, side_sign

# --- constructed, shared by both rules ------------------------------------
# A rule that needs its own value passes one; none does yet, except the
# break buffer, which is each rule's own definition of its break.

# The user's "mindestens 2-3 Kerzen auf der anderen Seite": bars *between*
# the break bar and the retest bar, each closing on the break side. Both
# ends of the phrase were measured on the VWAP rule (see vwap_retest);
# three was modestly better and is inside the range, so the upper end.
MIN_HOLD_BARS = 3

# How near the retest wick must come to the line to count as a test, as a
# fraction of price. Too tight and nothing qualifies; too loose and any bar
# in the neighbourhood counts.
TEST_BAND_PCT = 0.0015

# A close through the line ends the trade -- the break failed after all --
# and half comes off at the target with the rest trailed to break-even,
# the house management.
STOP_TRIGGER = STOP_ON_CLOSE
SCALE_OUT = 0.5

# Minimum reward for the risk, set by the user. A floor, not a target: a
# nearer level disqualifies the setup rather than moving the target.
MIN_TARGET_R = 2.0


def break_behind(bars, lines, sign, break_buffer_pct, min_hold_bars: int = MIN_HOLD_BARS) -> bool:
    """Whether a qualifying break-and-hold sits behind the retest bar.

    `lines` is the line as it stood at each bar, aligned with `bars`: the
    running VWAP for one rule, the (fixed) range boundary from the moment
    the range was complete for the other, None wherever the line did not
    exist yet.

    Walked backwards from the bar before the retest. Every bar walked must
    have closed on the break side -- one close back through means whatever
    break lies further behind already failed, and this "retest" is just
    price crossing a line it no longer owns. The walk ends at the crossing
    bar: the first whose predecessor closed on the other side. That bar
    must have closed clearly beyond, with at least `min_hold_bars` bars
    after it.

    Running out of line (None) before finding a crossing means the side was
    held from the moment the line existed -- nothing broke, so there is
    nothing to retest.
    """
    held = 0
    for i in range(len(bars) - 1, -1, -1):
        line = lines[i]
        if line is None:
            return False
        if sign * (bars[i].close - line) <= 0:
            return False

        crossed = i > 0 and lines[i - 1] is not None and sign * (bars[i - 1].close - lines[i - 1]) < 0
        if not crossed:
            held += 1
            continue

        return sign * (bars[i].close - line) >= line * break_buffer_pct and held >= min_hold_bars
    return False


def signal_for(
    ctx,
    name: str,
    side: str,
    lines: list,
    break_buffer_pct: float,
    line_label: str,
    max_entry_distance_pct: float | None = None,
) -> Signal | None:
    """A retest of a broken line, or None.

    `lines` is aligned with ctx.session_bars (see break_behind); the last
    entry is the line the retest bar is judged against.
    `max_entry_distance_pct` is a rule's own cap on how far from the line
    the retest close may sit -- passed by the VWAP rule, whose setup is
    defined by entering near the line; None leaves the 2:1 floor as the
    only thing refusing a bloated entry.
    """
    line = lines[-1] if lines else None
    if line is None or line <= 0:
        return None

    bar = ctx.bar
    sign = side_sign(side)

    # The retest itself: the wick reached the line, the body stayed on the
    # break side.
    if sign * (bar.close - line) <= 0:
        return None
    wick = bar.low if side == SIDE_LONG else bar.high
    if sign * (wick - line) > line * TEST_BAND_PCT:
        return None

    if not break_behind(ctx.session_bars[:-1], lines[:-1], sign, break_buffer_pct):
        return None

    entry = bar.close
    if max_entry_distance_pct is not None and abs(entry - line) / line > max_entry_distance_pct:
        return None

    # The retest bar's own wick: the print the market just defended. A bar
    # whose close sits on its own extreme (a doji at the line) has no risk
    # distance and nothing to size against, so it is skipped.
    stop = wick
    risk = sign * (entry - stop)
    if risk <= 0:
        return None

    target = next_level(ctx.levels, entry, sign)
    if target is None:
        return None
    if sign * (target - entry) < risk * MIN_TARGET_R:
        return None

    return Signal(
        strategy=name,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        reason=(
            f"{line_label} broken and held {side}, retested at {entry:.2f} "
            f"against {line:.2f}, target level {target:.2f}"
        ),
        side=side,
        stop_trigger=STOP_TRIGGER,
        scale_out=SCALE_OUT,
    )
