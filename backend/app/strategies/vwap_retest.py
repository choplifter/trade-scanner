"""VWAP break and retest, long and short.

The user's setup, as given: a candle breaks VWAP and closes clearly above
(or below) it; after at least two-three candles holding on the new side,
price comes back and tests the line -- the retest. That test is the entry,
in the break's direction, on the reading that a line which was defended
after being broken has changed sides.

Once the complement of VWAP Respect, which entered where the line held
repeatedly and never broke; that rule is removed (the user's call, after
its numbers -- its history is in git), and this is the VWAP rule that
remains. The pattern itself -- break, hold, return -- lives in
strategies.retest, shared with the opening-range rule, so the two stay
genuinely comparable; what is this file's own is the line (the running
session VWAP) and what "deutlich" means against it.

No opening window: the description does not give one, and a
line that breaks and is retested at 13:00 is the same shape as one at 10:00.
The session boundary is the only clock -- the break must have happened in
the session being traded, which falls out of session_vwaps being None
before the first regular print.

Measured at 2bp over the pinned 100-symbol liquid list and 40 days
(through 2026-08-21). As first built, with the stop measured off the line
(VWAP minus a 0.1% buffer):

                     n     expectancy   win     avg win   avg loss
    two holds      3574      -0.133    33.4%     +3.16     -1.78
    three holds    3030      -0.119    34.0%     +3.07     -1.76

Roughly flat before cost (-0.03% per trade): the entry sat cents from its
stop, 1R was a fraction of a percent, and 2bp per side ate it -- the
failure shape VWAP Respect documented. The user then asked for the stop at
the retest bar's own wick, which is where it now sits. Re-measured in one
window (through 2026-08-21), both placements:

                       n    expectancy   win     % win   % avg/trade
    stop off the line  2952    -0.107   34.3%    49.5%     -0.03
    stop at the wick   4093    -0.171   26.7%    47.7%     -0.01

Read the two columns separately, because they disagree and both are right.
Per trade in raw price the wick stop is the better rule (near flat). Per
unit of *risk* it is worse, and the mechanism matters: the wick shrinks 1R
so far that the 2:1 floor passes a third more setups (the floor is
risk-relative, so a smaller stop changes the population, not just the
exit), while the fixed 4bp round trip and the close-triggered stop's
overshoot balloon when divided by a tiny denominator (avg loss -4.8R
against a nominal 1). The touch trigger was then measured too and is far
worse (-1.014R, win 14.4% -- see retest.STOP_TRIGGER): a wick stop sits
where noise just was, and on touch every trade pays it. What remains as a
lever is the cost per trade, not the stop.
"""

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT
from app.strategies import retest
from app.strategies.context import Signal

NAME = "VWAP Retest"

# What "deutlich" -- clearly beyond -- means for the break close, as a
# fraction of price. Twice the range rules' minimum cross: the user's word
# asks for more than a close a fraction of a cent past the line.
BREAK_BUFFER_PCT = 0.001

# How far from VWAP the retest close may sit and still be this trade.
# Entering near the line is part of the setup's definition -- a "retest"
# whose body finished far away is a bar that merely wicked through the
# neighbourhood. Inherited from the removed VWAP Respect rule, which
# encoded "as close as possible to VWAP" with this value.
MAX_ENTRY_DISTANCE_PCT = 0.004

# The hold count, the test band, the wick stop, the 2:1 floor and the
# management all live in strategies.retest, shared with the opening-range
# retest. Referenced here so a reader of this file sees the whole rule.
MIN_HOLD_BARS = retest.MIN_HOLD_BARS
TEST_BAND_PCT = retest.TEST_BAND_PCT
SCALE_OUT = retest.SCALE_OUT


def _signal_for(ctx, side: str) -> Signal | None:
    return retest.signal_for(
        ctx,
        NAME,
        side,
        ctx.session_vwaps,
        break_buffer_pct=BREAK_BUFFER_PCT,
        line_label="VWAP",
        max_entry_distance_pct=MAX_ENTRY_DISTANCE_PCT,
    )


def evaluate(ctx) -> Signal | None:
    # Mutually exclusive -- a bar cannot close both above and below the
    # line -- so the order only fixes which is evaluated first.
    return _signal_for(ctx, SIDE_LONG) or _signal_for(ctx, SIDE_SHORT)
