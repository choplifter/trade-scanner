"""ORB Retest: the opening range breaks, holds, and the boundary is
retested -- the entry Aziz's crowd calls the safer ORB, taken at the
former ceiling instead of chasing the break.

The same pattern as the VWAP retest -- break, hold, return -- against a
different line: the opening-range boundary that was broken, the high for a
long and the low for a short. The pattern lives in strategies.retest,
shared with the VWAP rule; what is this file's own is the line and what a
break of it means, and the break is deliberately the *same event* the
Opening Range Breakout trades (a close beyond the boundary by the range
rules' shared buffer), so the two ORB entries describe one break two ways:
one enters on it, one waits to see it defended.

The window is the breakout's window, applied to the retest bar: a boundary
retested at 15:30 is not an opening-range trade in any meaningful sense,
and the break behind the retest falls inside automatically because it is
earlier. A retest of a still-forming range is impossible by construction:
the signal bar must postdate the range (is_complete below), and no bar
inside the window can be the break (see _signal_for).

Measured at 2bp over the pinned 100-symbol list and 40 days (through
2026-08-21): -0.428R on 366 signals, win 27.6%, and dead flat in raw price
(-0.00% per trade). The same anatomy as every wick-stop rule in this batch:
the pattern neither finds nor loses edge, and the tiny risk denominator
turns fixed cost and close-stop overshoot (avg loss -4.0R) into the whole
result. Rarer than the VWAP retest by a factor of ten, as an opening-range
boundary should be.
"""

from app.market_data import opening_range as orange
from app.services.market_clock import ET
from app.strategies import breakout, retest
from app.strategies.context import Signal
from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT

NAME = "ORB Retest"

OPENING_MINUTES = orange.OPENING_MINUTES

# What a break of the boundary is: the breakout rule's own definition,
# shared on purpose -- see the module docstring.
BREAK_BUFFER_PCT = breakout.BREAK_BUFFER_PCT

MIN_HOLD_BARS = retest.MIN_HOLD_BARS
TEST_BAND_PCT = retest.TEST_BAND_PCT
SCALE_OUT = retest.SCALE_OUT


def _signal_for(ctx, side: str, boundary: float) -> Signal | None:
    # The boundary is handed to every session bar, forming window included,
    # rather than withheld until the range is complete. Not a leak: a window
    # bar's close cannot exceed the range high it is itself part of, so no
    # bar inside the window can ever qualify as the crossing -- the earliest
    # possible break is the first bar after completion, which is exactly the
    # bar the Opening Range Breakout would trade. What the constant line
    # buys is that this first-possible break *counts*: judged against a None
    # line it would have no "other side" to have crossed from.
    lines = [boundary] * len(ctx.session_bars)
    return retest.signal_for(
        ctx,
        NAME,
        side,
        lines,
        break_buffer_pct=BREAK_BUFFER_PCT,
        line_label=f"{OPENING_MINUTES}m opening-range {'high' if side == SIDE_LONG else 'low'}",
    )


def evaluate(ctx) -> Signal | None:
    session_date = ctx.bar.timestamp.astimezone(ET).date()
    if not orange.is_complete(ctx.bar, session_date, OPENING_MINUTES):
        return None

    elapsed = breakout.minutes_since_open(ctx, session_date)
    if elapsed is None or elapsed > OPENING_MINUTES + breakout.BREAKOUT_WINDOW_MINUTES:
        return None

    high, low = orange.opening_range(ctx.session_bars, session_date, OPENING_MINUTES)
    if high is None or low is None or high <= low:
        # high == low is a range that did not move: every close breaks it,
        # which measures nothing -- same refusal as the breakout.
        return None

    # A long retests the broken high, a short the broken low. Mutually
    # exclusive on any one bar: the retest close must sit beyond its own
    # boundary, and a close cannot be above the high and below the low.
    return _signal_for(ctx, SIDE_LONG, high) or _signal_for(ctx, SIDE_SHORT, low)
