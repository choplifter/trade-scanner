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
from app.services.market_clock import ET
from app.strategies import breakout

NAME = "Opening Range Breakout"

# Five minutes, given by the user. At the resolution everything intraday here
# runs at that is exactly one bar -- a coincidence worth knowing rather than
# relying on; opening_range takes the length in minutes.
OPENING_MINUTES = orange.OPENING_MINUTES

# The break test, the stop, the target and the management all live in
# strategies.breakout, shared with the premarket-range rule. Two files that
# looked alike would drift, and comparing them is the reason both exist.


def evaluate(ctx):
    session_date = ctx.bar.timestamp.astimezone(ET).date()

    # The range has to be finished before it can be broken. Without this the
    # rule would compare a bar against a range that bar is still inside, and
    # every opening bar would trivially break its own high.
    if not orange.is_complete(ctx.bar, session_date, OPENING_MINUTES):
        return None

    high, low = orange.opening_range(ctx.session_bars, session_date, OPENING_MINUTES)
    return breakout.signal_for(
        ctx,
        NAME,
        high,
        low,
        range_label=f"{OPENING_MINUTES}m opening range",
        ready_after_minutes=OPENING_MINUTES,
    )
