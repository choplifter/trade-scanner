"""Opening Range Breakout, long and short (Andrew Aziz).

The method: measure the high and low of the first five minutes, then trade
the break of that range in whichever direction it goes. Hold to the target or
to a level, taking roughly half off on the approach and moving the stop to
break-even.

This rule is here partly *because* it is nearly free of interpretation. VWAP
Respect needs six constructed thresholds to say what "respect" and
"confirmation" mean, so a backtest of it measures the reconstruction as much
as the method. An opening range is a fact: the first five minutes are the
first five minutes, and a break of the high is a break of the high. What
remains constructed is listed below and is far shorter.

It also fails differently, which is the point of having both. VWAP Respect
enters cents from its stop, so 1R is about 0.3% of price and execution cost
eats it: measured, it needs under ~2.5bp per side to stay positive. This
risks the whole opening range instead -- structurally wider, so the same
cost is a much smaller fraction of R. If this loses money too, it will not
be because of the spread.
"""

from app.market_data import opening_range as orange
from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT, STOP_ON_CLOSE
from app.services.market_clock import ET, trading_hours_for
from app.strategies.context import Signal

NAME = "Opening Range Breakout"

# --- from the method -----------------------------------------------------
# Five minutes, given by the user. The target is the next level, never a
# multiple of risk, and half comes off there with the rest trailed to
# break-even -- the same management the VWAP rule uses, described as how
# Aziz handles a position rather than as something specific to that setup.
OPENING_MINUTES = orange.OPENING_MINUTES
SCALE_OUT = 0.5

# --- constructed: calibrate these ----------------------------------------
# A break is a *close* beyond the level, not a touch. A touch trigger fires
# on the wick that pokes through and reverses, which is the failure the
# opening range is most famous for, and it also cannot be told apart from
# noise at this resolution. The same reasoning as the VWAP rule's stop --
# see exit_rules on why the stop type is not a detail.
STOP_TRIGGER = STOP_ON_CLOSE

# How far beyond the range a close must sit to count, as a fraction of price.
# Zero would make a close one cent past the high a breakout.
BREAK_BUFFER_PCT = 0.0005

# How long after the range completes a break may still be taken. A break at
# 15:30 is not an opening-range break in any meaningful sense; where exactly
# it stops being one is a guess.
BREAKOUT_WINDOW_MINUTES = 60

# Minimum reward for the risk, as set by the user for the VWAP rule and
# carried here for consistency. A floor, not a target: a nearer level
# disqualifies the setup rather than moving the target, because a moved
# target sits at a price nothing about the stock chose.
MIN_TARGET_R = 2.0


def _next_level(levels, entry: float, sign: int) -> float | None:
    """The nearest level ahead of the entry, or None if there is none."""
    ahead = [level for level in levels if sign * (level - entry) > 0]
    if not ahead:
        return None
    return min(ahead, key=lambda level: sign * (level - entry))


def _signal_for(ctx, side: str, high: float, low: float) -> Signal | None:
    sign = 1 if side == SIDE_LONG else -1
    bar = ctx.bar
    level = high if side == SIDE_LONG else low

    # A close beyond the level, by more than the buffer.
    if sign * (bar.close - level) <= level * BREAK_BUFFER_PCT:
        return None

    entry = bar.close
    # The broken boundary itself, not the far end of the range. With a close
    # trigger this says something exact: price closed back inside the opening
    # range, so the break failed. The far end is the textbook placement and
    # was tried first -- it risks the whole range, which measured a median
    # 1.51% of price against a median distance to the next level of 0.39R.
    # Three signals cleared 2:1 out of 401 breaks. Moving the stop here took
    # the risk to 0.43% and 89 of them cleared it.
    #
    # Tighter placements exist and were measured too (the breakout bar's own
    # low: 0.24% risk, 121 clearing 2:1) and were not taken. That is the VWAP
    # rule's problem again -- at 0.24% risk a 10bp round trip costs 0.83R, so
    # the result would be a statement about the spread rather than the setup.
    stop = level
    risk = sign * (entry - stop)
    if risk <= 0:
        # Unreachable while the stop is the opposite end: a close above the
        # high is above the low by construction. Kept because the placement
        # above is a documented choice, and a stop just past the broken level
        # would make this case real -- ExitRule would otherwise refuse the
        # trade a layer later, where the error names the backtest instead of
        # the rule.
        return None

    target = _next_level(ctx.levels, entry, sign)
    if target is None:
        return None
    if sign * (target - entry) < risk * MIN_TARGET_R:
        return None

    return Signal(
        strategy=NAME,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        reason=(
            f"{OPENING_MINUTES}m opening range {low:.2f}-{high:.2f}, {side} break "
            f"closed at {entry:.2f}, target level {target:.2f}"
        ),
        side=side,
        stop_trigger=STOP_TRIGGER,
        scale_out=SCALE_OUT,
    )


def evaluate(ctx) -> Signal | None:
    session_date = ctx.bar.timestamp.astimezone(ET).date()

    # The range has to be finished before it can be broken. Without this the
    # rule would compare a bar against a range that bar is still inside, and
    # every opening bar would trivially "break" its own high.
    if not orange.is_complete(ctx.bar, session_date, OPENING_MINUTES):
        return None

    high, low = orange.opening_range(ctx.session_bars, session_date, OPENING_MINUTES)
    if high is None or low is None or high <= low:
        # high == low is a bar that did not move: every close is a break of
        # it, which measures nothing.
        return None

    # Measured from the opening bell, not from the first bar of the day --
    # session_bars starts in premarket, so the first bar can be 04:00 and the
    # window would have expired before the range even formed.
    hours = trading_hours_for(session_date)
    if hours is None:
        return None
    minutes_since_open = (ctx.bar.timestamp - hours[0]).total_seconds() / 60.0
    if minutes_since_open > OPENING_MINUTES + BREAKOUT_WINDOW_MINUTES:
        return None

    # Mutually exclusive: a close cannot be above the high and below the low.
    return _signal_for(ctx, SIDE_LONG, high, low) or _signal_for(ctx, SIDE_SHORT, high, low)
