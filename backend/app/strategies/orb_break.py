"""ORB Break: the opening-range breakout with the stop at the break bar's
own wick instead of the far end of the range.

The same setup as the Opening Range Breakout in every respect -- the same
range, the same close-beyond-the-buffer break, the same ATR veto, window,
target and management; the whole definition is opening_range_breakout.
signal_with_stop, called with the one thing this file chooses. Asked for
by the user as the candle-anchored counterpart of the retest rules' wick
stop: the break bar's low (for a long) is the print the break rose from,
and a return through it says the break failed without waiting for the far
end of the range to be taken out.

The comparison is the point of the file existing separately rather than
replacing the placement: the far end was itself a measured choice (see the
table referenced in opening_range_breakout), and two placements as two
rules lets the backtest hold them side by side.

Measured at 2bp over the pinned 100-symbol list and 40 days (through
2026-08-21), against the far-end sibling in the same window:

                    n    expectancy   win     % avg/trade
    far end        276     -0.057    42.8%      +0.34
    break wick    2991     -0.567    36.8%      -0.01

Not the same trades: the wick shrinks 1R so far that the 2:1 target floor
-- which is risk-relative -- passes ten times as many setups, most of them
aiming at levels the far-end rule would call too near. And on that tiny
denominator the 4bp round trip plus the close-triggered stop's overshoot
(avg loss -4.7R against a nominal 1) dominate the result. Per unit of risk
the far end remains the better ORB stop on this universe, by a wide
margin; this file is the measured record of why.
"""

from app.strategies import breakout
from app.strategies import opening_range_breakout as orb

NAME = "ORB Break"


def evaluate(ctx):
    return orb.signal_with_stop(ctx, NAME, breakout.STOP_BREAK_WICK)
