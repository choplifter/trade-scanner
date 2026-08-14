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

MOMENTUM_WINDOW = timedelta(minutes=15)


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
