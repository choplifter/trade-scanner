"""VWAP Open Range Break: the opening-range breakout whose break candle
takes out VWAP along with the boundary.

The user's name for it, read as the double break: the session VWAP sits
inside the opening range (or at worst between the boundary and the break
close), so the one close that clears the box clears the line too. The
plain ORB now only fires on the VWAP side at all (every signal passes
context.gate_by_vwap_side); what this rule adds is that the break candle
itself is the crossing -- price spent the range on the wrong side of the
line or straddling it, and this close is the first to own both.

Concretely, on top of the whole ORB definition: for a long, VWAP must lie
between the range low and the break close; for a short, between the range
high and the break close. VWAP already left far behind the box (below the
low on a long) means the range break broke nothing about the line, and
the setup is the plain ORB's, not this one's.

Everything else -- the five-minute range, the ATR veto, the
close-beyond-the-buffer break, the window, the far-end stop, the 2:1
floor, the half-off-and-trail management -- is opening_range_breakout.
signal_with_stop, shared so this cannot drift into trading a different
definition of the same break. The far-end stop is the measured ORB
default (see the table in orb_break for what the wick stop cost).

Not yet measured. When it is, the number belongs here -- run
scripts.strategy_backtest_report --strategy vwap_open_range_break against
the plain ORB in the same window.
"""

from app.scanners.exit_rules import SIDE_LONG
from app.services.market_clock import ET
from app.market_data import opening_range as orange
from app.strategies import breakout
from app.strategies import opening_range_breakout as orb

NAME = "VWAP Open Range Break"


def evaluate(ctx):
    signal = orb.signal_with_stop(ctx, NAME, breakout.STOP_FAR_END)
    if signal is None:
        return None

    vwap = ctx.vwap
    if vwap is None:
        # Unlike the ATR veto's asymmetry, an unknowable line declines the
        # *trade* here: the line being broken is this rule's whole subject,
        # so without it the setup does not exist. The plain ORB still covers
        # the break itself.
        return None

    session_date = ctx.bar.timestamp.astimezone(ET).date()
    high, low = orange.opening_range(ctx.session_bars, session_date, orb.OPENING_MINUTES)
    if high is None or low is None:
        return None

    # The line must be no further back than the far end of the box, and the
    # break close must have cleared it: VWAP in [low, close] for a long,
    # [close, high] for a short. The second bound repeats the global gate on
    # purpose -- the rule has to stand on its own when read or tested alone.
    behind = low if signal.side == SIDE_LONG else high
    if signal.sign * (vwap - behind) < 0:
        return None
    if signal.sign * (ctx.bar.close - vwap) <= 0:
        return None
    return signal
