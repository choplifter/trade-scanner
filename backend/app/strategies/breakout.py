"""The shared part of a range breakout: everything except which range.

Two rules use it -- the opening range and the premarket range -- and they
differ in one thing, the two prices that define the box. Everything after
that is identical: what counts as a break, where the stop goes, how far the
target has to be, how the position is managed.

Written as one implementation rather than two files that look alike, because
alike is exactly the state that rots. A change to the break test made in one
copy and not the other would leave two rules that a reader believes are
comparable and a backtest quietly says are not -- and comparing them is the
entire reason both exist.

Infrastructure, not a strategy: listed in loader._EXCLUDED, and the loader's
own test asserts that every non-strategy file here is.
"""

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT, STOP_ON_CLOSE
from app.services.market_clock import ET, trading_hours_for
from app.strategies import switches
from app.strategies.context import Signal, next_level

# --- constructed, shared by both rules, all still needing calibration -----
# Shared rather than copied because they describe the same mechanic. A rule
# that needs its own value passes one; none does yet.

# A break is a *close* beyond the level, not a touch. A touch trigger fires on
# the wick that pokes through and reverses, which is the failure a range
# breakout is most famous for, and at this resolution it cannot be told apart
# from noise. See exit_rules on why the stop type is not a detail.
STOP_TRIGGER = STOP_ON_CLOSE

# How far beyond the range a close must sit, as a fraction of price. Zero
# would make a close one cent past the high a breakout.
BREAK_BUFFER_PCT = 0.0005

# How long after the range becomes usable a break may still be taken. A break
# at 15:30 is not an opening break in any meaningful sense; where exactly it
# stops being one is a guess.
BREAKOUT_WINDOW_MINUTES = 60

# Minimum reward for the risk, set by the user. A floor, not a target: a
# nearer level disqualifies the setup rather than moving the target, because
# a moved target sits at a price nothing about the stock chose.
MIN_TARGET_R = 2.0

# The measured-move fallback (switchable -- see switches): when *no* level
# lies ahead of the entry, aim at entry + MEASURED_MOVE_R x risk instead of
# declining. Asked for by the user after a textbook break on a new-high day
# produced nothing -- the strongest breakouts have, by definition, no mapped
# structure above them, so "no level ahead means no trade" unlisted exactly
# the days the rule exists for.
#
# Risk-relative rather than the classic range-height projection, of
# necessity: the projected range (high + (high - low)) always sits *below*
# entry + risk for this rule's stops, so the classic form would fail the
# 2:1 floor it has to clear. Set to the floor itself -- the least
# constructed target that the rule's own arithmetic accepts.
#
# Deliberately only for "no level ahead", never for "a level is ahead but
# too near": a known level in the path is a wall the trade would have to
# trade through, and a constructed target beyond it would pretend the wall
# is not there. Those setups stay refused.
MEASURED_MOVE_R = MIN_TARGET_R

# Half off at the target with the rest trailed to break-even -- described as
# how Aziz handles a position rather than as specific to one setup.
SCALE_OUT = 0.5

# Where the stop sits, named rather than booleaned so a call site reads as
# the choice it is. FAR_END is the measured default (see the placement
# comment below); BREAK_WICK anchors it to the break bar's own extreme --
# the print the break rose from -- asked for by the user as the candle-
# anchored counterpart of the retest rules' wick stop.
STOP_FAR_END = "far-end"
STOP_BREAK_WICK = "break-wick"


def minutes_since_open(ctx, session_date) -> float | None:
    """How far past the opening bell this bar is, or None off a trading day.

    From the bell rather than from the first bar of the day: session_bars
    starts in premarket, so the first bar can be 04:00 and any window measured
    from it would have expired before the session began.
    """
    hours = trading_hours_for(session_date)
    if hours is None:
        return None
    return (ctx.bar.timestamp - hours[0]).total_seconds() / 60.0


def signal_for(
    ctx,
    name: str,
    high: float | None,
    low: float | None,
    range_label: str,
    window_minutes: float = BREAKOUT_WINDOW_MINUTES,
    ready_after_minutes: float = 0.0,
    stop_placement: str = STOP_FAR_END,
) -> Signal | None:
    """A break of [low, high], or None.

    `ready_after_minutes` is when the range becomes usable, measured from the
    bell: zero for a premarket range, which is finished when the bell rings,
    and the range length for one measured after the open. The window runs from
    there, so both rules get the same amount of opportunity rather than the
    same wall-clock deadline.
    """
    if high is None or low is None or high <= low:
        # high == low is a range that did not move: every close breaks it,
        # which measures nothing.
        return None

    session_date = ctx.bar.timestamp.astimezone(ET).date()
    elapsed = minutes_since_open(ctx, session_date)
    if elapsed is None or elapsed < ready_after_minutes:
        return None
    if elapsed > ready_after_minutes + window_minutes:
        return None

    # Mutually exclusive: a close cannot be above the high and below the low.
    for side, sign, level, opposite in (
        (SIDE_LONG, 1, high, low),
        (SIDE_SHORT, -1, low, high),
    ):
        if sign * (ctx.bar.close - level) <= level * BREAK_BUFFER_PCT:
            continue

        entry = ctx.bar.close
        if stop_placement == STOP_BREAK_WICK:
            # The break bar's own extreme: the print this break rose from.
            stop = ctx.bar.low if side == SIDE_LONG else ctx.bar.high
        else:
            # The far end of the range. Measured against the narrower
            # placements on this app's universe: a stop near the entry is
            # closed straight through on a name that moves 40% in a session,
            # so a nominal 1R of risk costs three. See opening_range_breakout
            # for the table.
            stop = opposite
        risk = sign * (entry - stop)
        if risk <= 0:
            # Unreachable while the stop is the far end -- a close above the
            # high is above the low by construction -- but real for the wick
            # stop, whose bar may close on its own extreme. Nothing to size
            # against either way, and ExitRule would otherwise refuse the
            # trade a layer later, where the error names the backtest
            # instead of the rule.
            return None

        target = next_level(ctx.levels, entry, sign)
        if target is None:
            if not switches.measured_move_target_enabled():
                return None
            target = entry + sign * risk * MEASURED_MOVE_R
            target_label = f"measured-move target {target:.2f} (no level ahead)"
        elif sign * (target - entry) < risk * MIN_TARGET_R:
            # A level ahead but too near stays a refusal even with the
            # fallback on -- see MEASURED_MOVE_R.
            return None
        else:
            target_label = f"target level {target:.2f}"

        return Signal(
            strategy=name,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            reason=(
                f"{range_label} {low:.2f}-{high:.2f}, {side} break closed at "
                f"{entry:.2f}, {target_label}"
            ),
            side=side,
            stop_trigger=STOP_TRIGGER,
            scale_out=SCALE_OUT,
        )
    return None
