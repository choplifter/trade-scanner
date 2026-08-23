"""VWAP ORB Line Stop: the line-crossing entry with the stop at the line
itself instead of the far end of the box.

The same setup as the VWAP Open Range Break in every respect -- the same
crossing, veto, window, target and management; the whole definition is
vwap_open_range_break.signal_with_stop, called with the one thing this
file chooses. Asked for by the user after the first measurement of the
crossing rule: 83% of its 15-minute positions died at the session close
with an average win of 0.55R against a 2:1 target, because a late entry
against the far end of the box makes 1R enormous and leaves no session to
earn it. The line is the thesis -- the session has volume-weightedly left
the box -- so a close back through it refutes the trade directly, and the
stop it implies is a fraction of the far end's.

The stop is where the line stood at entry, a fixed price: exit_rules and
the ticket speak fixed stops, not trailing lines.

Worth measuring, not assuming: every tiny-R placement in this package has
so far lost more in R than its wide sibling (see orb_break and the wick
tables in vwap_retest) because a small denominator inflates cost and
close-stop overshoot. What is different here is that this entry is a
confirmed trend statement rather than a first poke -- whether that earns
the tight stop its keep is exactly what the comparison exists to answer.
"""

from app.strategies import vwap_open_range_break as vorb

NAME = "VWAP ORB Line Stop"


def evaluate(ctx):
    return vorb.signal_with_stop(ctx, NAME, vorb.STOP_AT_LINE)
