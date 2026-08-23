"""VWAP break and retest, long and short.

The user's setup, as given: a candle breaks VWAP and closes clearly above
(or below) it; after at least two-three candles holding on the new side,
price comes back and tests the line -- the retest. That test is the entry,
in the break's direction, on the reading that a line which was defended
after being broken has changed sides.

The complement of VWAP Respect next door: Respect enters when the line
*held* repeatedly and never broke; this enters when it broke and the former
ceiling is retested as a floor (or the reverse). Both share the same
grammar -- a close is a verdict and a wick is a visit -- so a bar that
closes back through the line is never a retest, it is the failure of the
break, and it invalidates the setup rather than counting toward it.

The description names two numbers -- "clearly" beyond, and "2-3" candles --
and the floor of the user's own words is taken for the count. Everything
else below is constructed in the same places as vwap_respect and starts
from its values, so a calibration of one can be read against the other.

No opening window, unlike Respect: the description does not give one, and a
line that breaks and is retested at 13:00 is the same shape as one at 10:00.
The session boundary is the only clock -- the break must have happened in
the session being traded, which falls out of session_vwaps being None
before the first regular print.

Measured at 2bp over the pinned 100-symbol liquid list and 40 days
(through 2026-08-21), as first built:

                     n     expectancy   win     avg win   avg loss
    two holds      3574      -0.133    33.4%     +3.16     -1.78
    three holds    3030      -0.119    34.0%     +3.07     -1.76

Common -- ten times the signals of the range rules -- and negative at this
cost. Cost-free the average move is -0.03%: the setup is roughly flat and
the entry sits cents from its stop, so 1R is a fraction of a percent and
2bp per side eats a measurable slice of every trade -- the same failure
shape vwap_respect documents. The stop placement, not the pattern, is
where a calibration pass should start.
"""

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT, STOP_ON_CLOSE
from app.strategies.context import Signal, next_level, side_sign

NAME = "VWAP Retest"

# --- from the method -----------------------------------------------------
# A close back through the line ends the trade (the break failed after
# all), and half comes off at the target with the rest trailed to
# break-even -- the house management, same as every other rule here.
STOP_TRIGGER = STOP_ON_CLOSE
SCALE_OUT = 0.5

# --- from the user's words -----------------------------------------------
# "Mindestens 2-3 Kerzen auf der anderen Seite": bars *between* the break
# bar and the retest bar, each closing on the break side. Both ends of the
# phrase were measured (table above); three holds is modestly better and
# is inside the user's own range, so the upper end is taken.
MIN_HOLD_BARS = 3

# --- constructed: calibrate these ----------------------------------------
# What "deutlich" -- clearly beyond -- means for the break close, as a
# fraction of price. Twice the range rules' break buffer: the user's word
# asks for more than the minimum cross, and a close a fraction of a cent
# past the line is exactly what it is there to exclude.
BREAK_BUFFER_PCT = 0.001

# How near the retest wick must come to the line to count as a test.
# vwap_respect's band, for vwap_respect's reason: too tight and nothing
# qualifies, too loose and any bar in the neighbourhood counts.
TEST_BAND_PCT = 0.0015

# How far from VWAP the retest close may sit and still be this trade. The
# entry's nearness to the line is what keeps the risk small; beyond this
# the stop is far enough away that the setup is skipped rather than taken
# at worse risk. Mirrors vwap_respect.
MAX_ENTRY_DISTANCE_PCT = 0.004

# How far beyond VWAP the stop level sits. A *close* through it ends the
# trade, so this only sets what that close is measured against.
STOP_BUFFER_PCT = 0.001

# Minimum reward for the risk, set by the user, floor not target -- a
# nearer level disqualifies the setup rather than moving the target. Same
# argument as everywhere else it appears.
MIN_TARGET_R = 2.0


def _break_behind(bars, vwaps, sign) -> bool:
    """Whether a qualifying break-and-hold sits behind the retest bar.

    Walked backwards from the bar before the retest, each bar against the
    line as it stood at that bar. Every bar walked must have closed on the
    break side -- one close back through means whatever break lies further
    behind already failed and this "retest" is just price crossing a line
    it no longer owns. The walk ends at the crossing bar: the first whose
    predecessor closed on the other side. That bar must have closed
    clearly beyond, with at least MIN_HOLD_BARS bars after it.

    Running out of session (vwap None) before finding a crossing means the
    side was held from the first print -- a stock above VWAP all day never
    *broke* it, and there is nothing to retest.
    """
    held = 0
    for i in range(len(bars) - 1, -1, -1):
        vwap = vwaps[i]
        if vwap is None:
            return False
        if sign * (bars[i].close - vwap) <= 0:
            return False

        crossed = i > 0 and vwaps[i - 1] is not None and sign * (bars[i - 1].close - vwaps[i - 1]) < 0
        if not crossed:
            held += 1
            continue

        return sign * (bars[i].close - vwap) >= vwap * BREAK_BUFFER_PCT and held >= MIN_HOLD_BARS
    return False


def _signal_for(ctx, side: str) -> Signal | None:
    vwap = ctx.vwap
    if vwap is None or vwap <= 0:
        return None

    bar = ctx.bar
    sign = side_sign(side)

    # The retest itself: the wick reached the line, the body stayed on the
    # break side. A close through the line is the break failing, not a test
    # of it.
    if sign * (bar.close - vwap) <= 0:
        return None
    wick = bar.low if side == SIDE_LONG else bar.high
    if sign * (wick - vwap) > vwap * TEST_BAND_PCT:
        return None

    if not _break_behind(ctx.session_bars[:-1], ctx.session_vwaps[:-1], sign):
        return None

    entry = bar.close
    if abs(entry - vwap) / vwap > MAX_ENTRY_DISTANCE_PCT:
        return None

    stop = vwap * (1 - sign * STOP_BUFFER_PCT)
    risk = sign * (entry - stop)
    if risk <= 0:
        # A retest close inside the stop buffer: nothing to size against,
        # and ExitRule would refuse it a layer later with the backtest's
        # name on the error instead of the rule's.
        return None

    target = next_level(ctx.levels, entry, sign)
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
            f"VWAP broken and held {side}, retested at {entry:.2f} against "
            f"VWAP {vwap:.2f}, target level {target:.2f}"
        ),
        side=side,
        stop_trigger=STOP_TRIGGER,
        scale_out=SCALE_OUT,
    )


def evaluate(ctx) -> Signal | None:
    # Mutually exclusive -- a bar cannot close both above and below the
    # line -- so the order only fixes which is evaluated first.
    return _signal_for(ctx, SIDE_LONG) or _signal_for(ctx, SIDE_SHORT)
