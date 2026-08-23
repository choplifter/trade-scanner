"""VWAP respect at the open, long and short (Andrew Aziz).

The method as described:

  * At the open, watch the price action around VWAP and judge whether the
    stock *respects* the line -- approaches it and turns away rather than
    cutting through it.
  * Wait for confirmation of which way it is going, then take that side.
  * Enter as close to VWAP as possible, because that is what keeps the risk
    small.
  * The stop sits at a break of the line -- specifically a five-minute close
    through it, not a touch.
  * Hold to the profit target or to a new support/resistance level, taking
    roughly half off on the approach and moving the stop to break-even.

Everything above is the method. Every *number* below is a construction: the
description gives the shape of the setup, not thresholds, so these are
starting values to calibrate against scripts.strategy_backtest_report rather
than values to trust. They are grouped so it is clear which is which.

Two of the mechanics matter more than the thresholds do:

**The stop reads closes, not touches.** An entry "as close as possible to
VWAP" sits cents from its own stop. Scored on touch, such a trade is stopped
out by ordinary noise within a bar or two, and the backtest would report a
strategy nobody described. STOP_ON_CLOSE is what makes the measurement match
the method -- and it carries its own honest cost, since a close-based stop is
not in the market and can therefore lose several R on one bar.

**A test of the line is not a break of it.** Respect is counted here as a bar
whose *wick* reached the line while its *body* stayed on one side. A bar that
closed through the line is not a test; it is the break the stop exists for,
and it resets the count.
"""

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT, STOP_ON_CLOSE
from app.strategies.context import Signal, next_level, side_sign

NAME = "VWAP Respect"

# --- from the method -----------------------------------------------------
# A five-minute close through the line ends the trade, and half comes off at
# the target with the rest trailed to break-even. Both are described.
STOP_TRIGGER = STOP_ON_CLOSE
SCALE_OUT = 0.5

# --- constructed: calibrate these ----------------------------------------
# How long "at the open" lasts.
OPENING_WINDOW_MINUTES = 90

# How many tests of the line count as respect before a side may be taken.
# Two is the smallest number that distinguishes a level from a coincidence.
MIN_TESTS = 2

# How near a wick must come to count as a test, as a fraction of price. Too
# tight and nothing qualifies; too loose and any bar in the neighbourhood
# counts.
TEST_BAND_PCT = 0.0015

# How far from VWAP an entry may still be taken -- the constant that encodes
# "as close as possible". Beyond it the stop is too far away for the trade to
# be the one described, so the setup is skipped rather than taken at worse
# risk.
MAX_ENTRY_DISTANCE_PCT = 0.004

# How far beyond VWAP the stop level sits. It is a *close* through it that
# ends the trade, so this only sets what that close is measured against.
STOP_BUFFER_PCT = 0.001

# The target is the next level in the trade's direction -- never a multiple of
# risk. That is the method, not a setting, which is why there is no TARGET_R
# any more: an R multiple placed the target wherever the stop happened to be,
# and the stop here is a few cents from VWAP, so it aimed at a price nothing
# about the stock had chosen.
#
# The one constructed part is the floor below. A level barely above the entry
# is a real level and still a bad trade: risking one to make a fraction is
# what a small stop is *for* avoiding. Setups whose nearest level is closer
# than this are skipped rather than taken at a poor ratio.
#
# 2:1 as the minimum, set by the user. It is a floor and not a target: the
# trade is still aimed at the level, so a nearer level than 2R disqualifies
# the setup rather than moving the target to 2R -- moving it would put the
# exit at a price nothing about the stock had chosen, which is exactly what
# replacing the R multiple was meant to stop.
MIN_TARGET_R = 2.0


def _session_open_index(ctx) -> int | None:
    """The first bar the regular session had started for.

    session_vwaps is None until something has traded inside the session, so
    the first non-None entry *is* the open. Derived rather than compared
    against a literal 09:30 so a late first print, or a half day, cannot
    silently move the window.
    """
    for i, vwap in enumerate(ctx.session_vwaps):
        if vwap is not None:
            return i
    return None


def _count_tests(bars, vwaps, side: str) -> int:
    """Wicks that reached the line, since the last body that closed through it.

    The reset is the point: once price has closed on the other side, whatever
    respect came before describes a level that no longer holds.
    """
    sign = side_sign(side)
    tests = 0
    for bar, vwap in zip(bars, vwaps):
        if vwap is None:
            continue

        if sign * (bar.close - vwap) < 0:
            tests = 0
            continue

        wick = bar.low if side == SIDE_LONG else bar.high
        if sign * (wick - vwap) <= vwap * TEST_BAND_PCT:
            tests += 1

    return tests


def _signal_for(ctx, side: str) -> Signal | None:
    vwap = ctx.vwap
    if vwap is None or vwap <= 0:
        return None

    bar = ctx.bar
    sign = side_sign(side)

    # Confirmation: this bar closed on our side of the line, and closed in
    # our direction. Both, because a close on the right side of VWAP that is
    # still going the wrong way confirms nothing.
    if sign * (bar.close - vwap) <= 0:
        return None
    if sign * (bar.close - bar.open) <= 0:
        return None

    # Respect must be established *before* the confirmation bar, so the count
    # excludes it.
    if _count_tests(ctx.session_bars[:-1], ctx.session_vwaps[:-1], side) < MIN_TESTS:
        return None

    entry = bar.close
    if abs(entry - vwap) / vwap > MAX_ENTRY_DISTANCE_PCT:
        return None

    stop = vwap * (1 - sign * STOP_BUFFER_PCT)
    risk = sign * (entry - stop)
    if risk <= 0:
        # The confirmation closed on the wrong side of its own stop, which a
        # small buffer can produce when price sits right on the line. No risk
        # distance means nothing to size against, and ExitRule would refuse
        # it a layer later.
        return None

    target = next_level(ctx.levels, entry, sign)
    if target is None:
        # No structure ahead means no target, and the method has no other one
        # to fall back on. Skipped rather than given an invented number.
        return None
    if sign * (target - entry) < risk * MIN_TARGET_R:
        return None

    return Signal(
        strategy=NAME,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        reason=(
            f"VWAP respected, {side} confirmed at {entry:.2f} against VWAP "
            f"{vwap:.2f}, target level {target:.2f}"
        ),
        side=side,
        stop_trigger=STOP_TRIGGER,
        scale_out=SCALE_OUT,
    )


def evaluate(ctx) -> Signal | None:
    open_index = _session_open_index(ctx)
    if open_index is None:
        return None

    opened_at = ctx.session_bars[open_index].timestamp
    if (ctx.bar.timestamp - opened_at).total_seconds() / 60.0 > OPENING_WINDOW_MINUTES:
        return None

    # The two are mutually exclusive -- a bar cannot close both above and
    # below the line -- so the order only fixes which is evaluated first.
    return _signal_for(ctx, SIDE_LONG) or _signal_for(ctx, SIDE_SHORT)
