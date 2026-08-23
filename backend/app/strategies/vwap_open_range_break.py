"""VWAP Open Range Break: the session VWAP itself crosses out of the
opening range -- the *line* breaks the box, not a candle.

The user's rule, reached on the third build, and the two wrong builds are
part of this file's record because both were measured. As first built the
signal was a price candle breaking the box while VWAP rode along -- and
the session VWAP, born at the bell inside the box, rode along on every
break, so the rule differed from the plain ORB by one trade in 1,313.
Re-anchored at the premarket VWAP the line was honest but the pinned
liquid universe gaps too little for it to stray: 23 binding trades in
1,313. (2bp, pinned 100-symbol list, 40 days through 2026-08-21,
measured-move fallback and VWAP-side gate on, both anchors within a few
hundredths of an R of the ORB itself.) The user then corrected the
reading: the VWAP was never meant to *accompany* a break -- it was meant
to BE the break.

That rule is this file now, and it is a different animal from the ORB.
The session VWAP starts inside the box by construction (it is the
volume-weighted mean of the prints that drew the box), and it is heavy:
one spike candle barely moves it. It leaves the box only when the session
has persistently, volume-weightedly traded beyond the range -- so the
crossing is a trend statement no single candle can fake, and it
necessarily happens later than the candle break the ORB trades.

The signal fires on the crossing bar: the session VWAP beyond the
boundary by the range rules' shared buffer, having not been beyond it on
the previous bar. Long above the high, short below the low. Entry is the
crossing bar's close, the stop is the far end of the range (the measured
ORB placement), the target comes from the shared resolve_target (next
level, 2:1 floor, measured-move fallback), inside the same
window-after-completion and behind the same ATR veto as the ORB family --
shared so the rules stay genuinely comparable.

Not yet measured in this form. When it is, the numbers belong here.
"""

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT
from app.services.market_clock import ET
from app.market_data import opening_range as orange
from app.strategies import breakout, switches
from app.strategies import opening_range_breakout as orb
from app.strategies.context import Signal

NAME = "VWAP Open Range Break"


def evaluate(ctx) -> Signal | None:
    session_date = ctx.bar.timestamp.astimezone(ET).date()
    minutes = switches.opening_range_minutes()

    if not orange.is_complete(ctx.bar, session_date, minutes):
        return None
    elapsed = breakout.minutes_since_open(ctx, session_date)
    if elapsed is None or elapsed > minutes + breakout.BREAKOUT_WINDOW_MINUTES:
        return None

    high, low = orange.opening_range(ctx.session_bars, session_date, minutes)
    if high is None or low is None or high <= low:
        return None
    if orb.range_vetoed_by_atr(ctx, high, low):
        return None

    vwap = ctx.vwap
    # The line one bar earlier, to make the crossing an event rather than a
    # state: a rule firing on every bar the line spends outside the box
    # would hand the chart a drifting entry and the backtest a smeared one.
    prev = ctx.session_vwaps[-2] if len(ctx.session_vwaps) >= 2 else None
    if vwap is None or prev is None:
        return None

    # Mutually exclusive: the line cannot cross the high and the low on the
    # same bar upward and downward at once.
    for side, sign, level, opposite in (
        (SIDE_LONG, 1, high, low),
        (SIDE_SHORT, -1, low, high),
    ):
        # Beyond by the shared buffer now, not beyond it before. The same
        # buffer on both reads, so a line hovering at the boundary is one
        # crossing, not a flicker of them.
        beyond = sign * (vwap - level) > level * breakout.BREAK_BUFFER_PCT
        was_beyond = sign * (prev - level) > level * breakout.BREAK_BUFFER_PCT
        if not beyond or was_beyond:
            continue

        entry = ctx.bar.close
        stop = opposite
        risk = sign * (entry - stop)
        if risk <= 0:
            # Real here, unlike the candle break: the crossing bar's close
            # can sit anywhere, including back beyond the far end of the
            # range on a violent reversal. Nothing to size against.
            return None

        resolved = breakout.resolve_target(ctx, entry, sign, risk)
        if resolved is None:
            return None
        target, target_label = resolved

        return Signal(
            strategy=NAME,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            reason=(
                f"{minutes}m opening range {low:.2f}-{high:.2f}, session VWAP "
                f"crossed {'above the high' if side == SIDE_LONG else 'below the low'} "
                f"at {vwap:.2f}, {target_label}"
            ),
            side=side,
            stop_trigger=breakout.STOP_TRIGGER,
            scale_out=breakout.SCALE_OUT,
        )
    return None
