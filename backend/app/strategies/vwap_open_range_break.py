"""VWAP Open Range Break: the opening-range breakout whose break candle
takes out the *premarket-anchored* VWAP along with the boundary.

The premarket anchor is the second version of this rule, and it is what
measurement forced. As first built, the line was the session VWAP -- and
side by side with the plain ORB (2bp, pinned 100-symbol list, 40 days
through 2026-08-21, measured-move fallback and VWAP-side gate on) the
qualification bound exactly once in 1,313 trades:

                     n    expectancy   win     % avg/trade
    ORB, 5m boxes  1313     -0.149    40.4%      -0.03
    session VWAP   1312     -0.149    40.4%      -0.02
    ORB, 15m        857     -0.105    42.6%      -0.20
    session VWAP    857     -0.105    42.6%      -0.20

Structural, not a bug: the session VWAP is anchored at the opening bell,
so during the breakout window it has barely had time to leave the opening
range -- the line is almost always inside the box, and a close that
clears the box clears the line for free. For the qualification to mean
anything the line has to be one that can sit far from the box, and
ctx.premarket_vwap is that line: anchored at the premarket open, it has
been accumulating for hours by the bell, and on a gapper it can sit far
above or below wherever the auction put the range. See
app.market_data.vwap for the two anchors and the case that forced
carrying both.

The rule, restated for this line: on top of the whole ORB definition,
the premarket VWAP must lie between the far end of the range and the
break close -- the box (or the break candle) contains the line, so the
close that breaks the range is also the close that reclaims (long) or
loses (short) the premarket VWAP. The line already left far behind the
boundary means the range break broke nothing about it, and the setup is
the plain ORB's, not this one's.

No premarket trading means no premarket VWAP (None), and that declines
the trade rather than the veto: the line being broken is this rule's
whole subject, so without it the setup does not exist. The plain ORB
still covers the break itself.

Everything else -- the switchable 5/15-minute range, the ATR veto, the
close-beyond-the-buffer break, the window, the far-end stop, the 2:1
floor, the half-off-and-trail management -- is opening_range_breakout.
signal_with_stop, shared so this cannot drift into trading a different
definition of the same break. Not yet measured on this anchor; when it
is, the numbers belong here next to the session-VWAP table above.
"""

from app.scanners.exit_rules import SIDE_LONG
from app.services.market_clock import ET
from app.market_data import opening_range as orange
from app.strategies import breakout, switches
from app.strategies import opening_range_breakout as orb

NAME = "VWAP Open Range Break"


def evaluate(ctx):
    signal = orb.signal_with_stop(ctx, NAME, breakout.STOP_FAR_END)
    if signal is None:
        return None

    vwap = ctx.premarket_vwap
    if vwap is None:
        return None

    session_date = ctx.bar.timestamp.astimezone(ET).date()
    high, low = orange.opening_range(
        ctx.session_bars, session_date, switches.opening_range_minutes()
    )
    if high is None or low is None:
        return None

    # The line must be no further back than the far end of the box, and the
    # break close must have cleared it: premarket VWAP in [low, close] for a
    # long, [close, high] for a short.
    behind = low if signal.side == SIDE_LONG else high
    if signal.sign * (vwap - behind) < 0:
        return None
    if signal.sign * (ctx.bar.close - vwap) <= 0:
        return None
    return signal
