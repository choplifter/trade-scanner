"""Opening Range Breakout, long and short (Andrew Aziz).

The method: measure the high and low of the first five minutes, then trade
the break of that range in whichever direction it goes. Hold to the target or
to a level, taking roughly half off on the approach and moving the stop to
break-even.

This rule is here partly *because* it is nearly free of interpretation. VWAP
Respect (a rule since removed; its measured history is in git) needed six
constructed thresholds to say what "respect" and "confirmation" meant, so a
backtest of it measured the reconstruction as much as the method. An opening
range is a fact: the first five minutes are the first five minutes, and a
break of the high is a break of the high. What remains constructed is listed
below and is far shorter.

It also fails differently, which was the point of having both and still
separates this from the VWAP retest: a rule entering cents from its stop has
1R around 0.3% of price and execution cost eats it -- VWAP Respect needed
under ~2.5bp per side to stay positive. This risks the whole opening range
instead -- structurally wider, so the same cost is a much smaller fraction
of R. If this loses money too, it will not be because of the spread.
"""

from app.market_data import opening_range as orange
from app.services.market_clock import ET
from app.strategies import breakout

NAME = "Opening Range Breakout"

# Five minutes, given by the user. At the resolution everything intraday here
# runs at that is exactly one bar -- a coincidence worth knowing rather than
# relying on; opening_range takes the length in minutes.
OPENING_MINUTES = orange.OPENING_MINUTES

# Aziz's precondition on the box itself: the opening range must be "deutlich
# geringer" -- well below -- the stock's daily ATR, read off his scanner's
# ATR column. A range that already covers most of the day's typical travel
# has spent the move the breakout is supposed to catch. He gives no number;
# this fraction is a construction, chosen so "half the usual day inside five
# minutes" is where the rule stops believing there is room left.
#
# Measured at 2bp over a pinned 100-symbol liquid universe and 40 days
# (through 2026-08-21): at 0.5 the veto removes 14 of 394 signals and moves
# expectancy +0.002R; tightened to 0.33 it removes the better trades too
# (-0.109R on 243, against -0.075R on 282 at 0.5). Barely binding on liquid
# names is the expected shape -- their five-minute box is a small fraction
# of ATR -- and the veto exists for the gapper whose box is not.
MAX_RANGE_ATR_FRACTION = 0.5

# The break test, the stop, the target and the management all live in
# strategies.breakout, shared with the premarket-range rule. Two files that
# looked alike would drift, and comparing them is the reason both exist.


def signal_with_stop(ctx, name: str, stop_placement: str):
    """The ORB setup -- completed range, ATR veto, window -- with the stop
    placement a caller chooses. The setup lives once, here, so this rule
    and the wick-stop variant (orb_break) cannot drift into trading two
    different definitions of the same break.
    """
    session_date = ctx.bar.timestamp.astimezone(ET).date()

    # The range has to be finished before it can be broken. Without this the
    # rule would compare a bar against a range that bar is still inside, and
    # every opening bar would trivially break its own high.
    if not orange.is_complete(ctx.bar, session_date, OPENING_MINUTES):
        return None

    high, low = orange.opening_range(ctx.session_bars, session_date, OPENING_MINUTES)

    # The veto declines when the ATR is unknowable (ctx.daily_atr is None:
    # the live scanner's one-session cache, a fresh listing) rather than
    # refusing the trade. Deliberate, and the same asymmetry the scanner
    # already runs under for levels: a marker that over-flags invites a look
    # at the chart, where the ATR *is* computable and this veto binds --
    # while a veto that cannot be computed refusing everything would unlist
    # the setup exactly where nobody can see why.
    if (
        ctx.daily_atr is not None
        and high is not None
        and low is not None
        and high - low >= ctx.daily_atr * MAX_RANGE_ATR_FRACTION
    ):
        return None

    return breakout.signal_for(
        ctx,
        name,
        high,
        low,
        range_label=f"{OPENING_MINUTES}m opening range",
        ready_after_minutes=OPENING_MINUTES,
        stop_placement=stop_placement,
    )


def evaluate(ctx):
    return signal_with_stop(ctx, NAME, breakout.STOP_FAR_END)
