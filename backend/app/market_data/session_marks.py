"""Intraday reference prices: today's premarket range, and the prior
session's high and low.

The other kind of level. app.market_data.levels finds structure the hard way
-- pivots, clustering, counting separate returns -- and answers "where has
this stock repeatedly turned over sixty days". These are read straight off
the tape and answer something much simpler: "where did the night, and
yesterday, leave the boundaries". No threshold, no counting. Yesterday's high
is a fact, not a finding.

That difference is why both exist. Structure levels sit multiple percent away
and a trade entered at VWAP rarely reaches one inside a session; these sit
where the day is actually being fought, which is what a rule holding "to the
next level" is aiming at.

Parameterised by session date rather than by "now", which is the whole reason
this is not just the two indicator files. A chart asks for today; a replay
asks for the day it is walking, and must get the answer that was knowable
then. Both are complete before the opening bell -- the premarket ends at the
open and yesterday ended yesterday -- so neither leaks the future into a
session being replayed.
"""

from app.services.market_clock import ET, trading_hours_for


def _et_date(bar):
    return bar.timestamp.astimezone(ET).date()


def latest_session_date(bars: list):
    """The session the bars actually describe, which is not always today.

    Anchoring an intraday reference to the wall clock breaks the moment the
    clock and the data disagree -- on a Saturday there is no "today's
    premarket", so the line vanishes while the chart still shows Friday, and
    a strategy that aimed at Friday's premarket high is left pointing at a
    level the reader cannot see. Observed exactly that way: ALOY's target was
    11.43, its premarket high, with no premarket line drawn anywhere.

    During a live session this returns today and nothing changes.
    """
    return bars[-1].timestamp.astimezone(ET).date() if bars else None


def premarket_range(bars: list, session_date) -> tuple[float | None, float | None]:
    """High and low of `session_date`'s premarket, before the opening bell.

    The open comes from the calendar rather than a literal 09:30 so a half
    day is measured against its own bell.
    """
    hours = trading_hours_for(session_date)
    if hours is None:
        return None, None
    market_open = hours[0]

    session = [
        bar
        for bar in bars
        if _et_date(bar) == session_date and bar.timestamp.astimezone(ET) < market_open
    ]
    if not session:
        return None, None
    return max(b.high for b in session), min(b.low for b in session)


def prior_session_range(bars: list, session_date) -> tuple[float | None, float | None]:
    """PDH/PDL: the previous trading day's *regular-session* high and low.

    Extended-hours prints are excluded on purpose, matching the chart
    indicator: they are thin enough that one stray tick sets a "high" no
    meaningful volume traded at, and the premarket has its own range above.

    "Previous" is the most recent date before `session_date` that actually
    traded, not `session_date` minus one -- otherwise every Monday would
    measure a Sunday and report nothing.
    """
    earlier = sorted({_et_date(bar) for bar in bars if _et_date(bar) < session_date}, reverse=True)

    for day in earlier:
        hours = trading_hours_for(day)
        if hours is None:
            continue
        market_open, market_close = hours
        session = [
            bar
            for bar in bars
            if _et_date(bar) == day and market_open <= bar.timestamp.astimezone(ET) <= market_close
        ]
        if session:
            return max(b.high for b in session), min(b.low for b in session)
    return None, None


def marks_for_session(bars: list, session_date) -> list[float]:
    """Every intraday reference price knowable at `session_date`'s open.

    Absent ones are dropped rather than reported as zero: a symbol that did
    not trade premarket has no premarket range, and a zero would read as a
    level at zero and pull a target down to it.
    """
    marks = [*premarket_range(bars, session_date), *prior_session_range(bars, session_date)]
    return [price for price in marks if price is not None and price > 0]
