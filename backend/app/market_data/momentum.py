"""Trailing-window price momentum from a symbol's own minute bars -- e.g.
"how much has this stock moved in just the last 15 minutes," independent
of its pct_change since the prior close.

Shared by AI trade-idea context (app.ai.trade_ideas) and the live
scanner's momentum cache (app.scanners.momentum_cache) so a symbol still
visibly accelerating right now can be told apart from one that already
ran earlier in the session and has since gone flat or reversed -- RVOL
alone can't do this, since it's cumulative volume vs. a full-day average
and mechanically rises through the session regardless of *when* that
volume actually printed (see formulas.rvol's docstring).
"""

from datetime import timedelta

from app.services.market_clock import ET, trading_hours_for

MOMENTUM_WINDOW = timedelta(minutes=15)


def is_regular_session_bar(bar) -> bool:
    """True when `bar` falls inside its own day's official NYSE regular
    session. Uses trading_hours_for (the real calendar) rather than a
    hardcoded 09:30-16:00 so early-close days (e.g. the half day after
    Thanksgiving) are handled correctly.

    Momentum is deliberately a regular-session-only concept: outside it,
    a "15-minute move" is dominated by session-boundary discontinuities
    and thin prints rather than real momentum. A 2026-07-27 APLD example
    found in the backtest measured +5.34% "in 15 minutes" purely by
    comparing a 300-share after-hours print against the last
    regular-session close 15 minutes (and one market close) earlier.
    """
    ts_et = bar.timestamp.astimezone(ET)
    hours = trading_hours_for(ts_et.date())
    if hours is None:
        return False
    market_open, market_close = hours
    return market_open <= ts_et < market_close


def same_trading_day(bar, reference) -> bool:
    """True when both bars fall on the same ET calendar date -- the guard
    that keeps a trailing momentum window from reaching back across a
    market close into the *previous* day's session, which is what makes
    the boundary artifact above possible.

    Deliberately allows same-day premarket into the window, even though
    only regular-session bars may *trigger* (see is_regular_session_bar).
    The two cases aren't symmetric: an after-hours trigger bar is the
    problem (thin, post-close, often a single print), whereas an
    opening-range trigger at 09:35 is a real, liquid regular-session bar
    whose 15-minute reference price simply happens to sit in premarket --
    a genuine traded price. Excluding premarket from the window entirely
    would blank out 09:30-09:45 ET, which in a 10-symbol/30-day sample was
    45 of 84 picks, i.e. most of the signal.
    """
    return bar.timestamp.astimezone(ET).date() == reference.timestamp.astimezone(ET).date()


def pct_change_over_window(bars: list, window: timedelta) -> float | None:
    """% change from the closing price `window` before the latest bar to
    the latest bar's own close. None when there isn't yet `window` worth
    of bars to compare against (e.g. just after premarket open) or `bars`
    is empty.
    """
    if not bars:
        return None
    latest_bar = bars[-1]
    cutoff = latest_bar.timestamp - window
    reference_bar = next((b for b in reversed(bars) if b.timestamp <= cutoff), None)
    if reference_bar is None or not reference_bar.close:
        return None
    return round((latest_bar.close - reference_bar.close) / reference_bar.close * 100, 2)
