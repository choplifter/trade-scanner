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

Measured, and the answer is the same one every tiny-R placement in this
package has given (2bp, pinned 100-symbol list, 40 days through
2026-08-21, measured-move fallback and VWAP-side gate on), against the
far-end sibling in the same window:

                    n    expectancy   win     % avg/trade   exits at close
    far end, 5m    389     -0.097    41.6%      -0.24            78%
    line, 5m       572     -0.398    38.6%      -0.02            32%
    far end, 15m   189     -0.063    47.6%      -0.39            83%
    line, 15m      239     -0.312    38.1%      -0.06            42%

Read both columns; they disagree and both are right. The tight stop fixes
what the far end suffered from -- trades now resolve inside the session
(winners actually reach the 2:1 target, avg win 1.96R) and in raw price
this is the flattest rule in the family (-0.02%). But per unit of risk it
is far worse, by the usual mechanism: the small denominator passes half
again as many setups through the risk-relative floor and turns the fixed
cost plus close-stop overshoot into the whole result (avg loss -1.89R
against a nominal 1). A confirmed-trend entry did not break the pattern;
the confirmation arrives at a price that sits, once again, cents from the
line that confirmed it.
"""

from app.strategies import vwap_open_range_break as vorb

NAME = "VWAP ORB Line Stop"


def evaluate(ctx):
    return vorb.signal_with_stop(ctx, NAME, vorb.STOP_AT_LINE)
