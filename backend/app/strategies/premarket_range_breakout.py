"""Premarket Range Breakout, long and short.

The same rule as the opening range breakout in every respect except the box:
the range is the high and low set before the bell, not the one set in the
first five minutes. Everything else -- the close-triggered break, the buffer,
the stop at the far end, the 2:1 floor, the target from
app.market_data.levels, the half-off-and-trail management -- comes from
strategies.breakout, so the two rules stay genuinely comparable.

Not from the book. This is the user's suggestion, tested before it was built,
and it is the only variant so far that survived the test.

Measured over 100 liquid symbols and 40 days at 2bp per side:

                        n    expectancy   win     avg loss
    first 5 minutes   455       -0.058   43.7%      -0.88
    premarket range   397       +0.121   47.1%      -1.02

and, split into two disjoint halves of that window:

                        early              late
    first 5 minutes   -0.153 (n=221)    +0.033 (n=234)
    premarket range   +0.152 (n=164)    +0.099 (n=233)

The premarket version is positive in both halves; the five-minute one changes
sign between them, which is what a result with nothing behind it looks like.
Break-even sits near 4.7bp per side.

**It works on liquid names and not on gappers, which is the opposite of the
reasoning that prompted the test.** On the scanner's own gapper universe it
returns -0.612R on 39 signals. The measured widths say why: on liquid names
the premarket range is 1.61% of price against the opening range's 1.90%, so
it is the tighter, better-defined box -- while on a $3 stock that traded 40%
overnight it is not a level at all, it is a crater.

Still a weak edge on one period, with parameters that are constructions.
"""

from app.market_data import session_marks
from app.services.market_clock import ET
from app.strategies import breakout

NAME = "Premarket Range Breakout"

# Zero, unlike the opening range: the premarket is finished the moment the
# bell rings, so the box is ready to be broken from the first regular bar.
READY_AFTER_MINUTES = 0.0


def evaluate(ctx):
    session_date = ctx.bar.timestamp.astimezone(ET).date()
    high, low = session_marks.premarket_range(ctx.session_bars, session_date)
    return breakout.signal_for(
        ctx,
        NAME,
        high,
        low,
        range_label="premarket range",
        ready_after_minutes=READY_AFTER_MINUTES,
    )
