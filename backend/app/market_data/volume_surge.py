"""Is this symbol's volume accelerating *right now*?

RVOL cannot answer that. It is cumulative volume since the open over a
full-day average, so it only ever climbs through the session (see
formulas.rvol) -- a stock that traded heavily at 09:45 and has been dead
since still reads high at 15:30. What a late-session scan actually wants is
"more is printing in the last hour than should be", which is a *rate*
question, not a cumulative one.

Two ratios, because they answer different questions and one of them is a
trap on its own:

- `surge_ratio` compares the trailing window against the window before it.
  Purely self-relative, needs no reference data. But volume is U-shaped
  across a session -- measured against SPY's own curve, ~43% of a typical
  day is done by noon and ~100% by 15:55, so the last hour is a large
  slice for *everything*. Near the close this ratio is above 1 for most of
  the market at once, which is exactly when an end-of-day scan would be
  run. Useful as a description, misleading as a filter.

- `windowed_rvol` compares the trailing window against the volume normally
  traded in that same clock window, taken from the intraday volume profile
  (app.market_data.volume_profile) that already models the U-shape. "3x"
  here means three times the usual 15:00-16:00 volume, not three times a
  quiet lunch hour. This is the one to screen on.

Both are computed from the 5-minute bars the momentum cache already fetches
for ranked and screened symbols, so neither costs an extra API call.
"""

from datetime import datetime, timedelta

from app.market_data.bars import MOMENTUM_BAR_MINUTES
from app.market_data.momentum import is_regular_session_bar, same_trading_day
from app.market_data.volume_profile import fraction_at

DEFAULT_WINDOW = timedelta(minutes=60)
# A bar's timestamp is the start of its interval, so the window that "ends
# now" ends one bar-length past the latest bar's stamp. Taken from the
# constant the rest of the app keys off rather than inferred from the gap
# between two bars, which a feed gap or a halt would silently distort.
_BAR_LENGTH = timedelta(minutes=MOMENTUM_BAR_MINUTES)


def latest_session_bar(bars: list):
    """The most recent bar that is inside its own day's regular session, or
    None. Everything here anchors on this rather than the wall clock: the
    poll loop runs on its own cadence and the feed lags it, so "now" from
    the clock would routinely name a window the bars don't cover yet.
    """
    for bar in reversed(bars):
        if is_regular_session_bar(bar):
            return bar
    return None


def window_volume(bars: list, start: datetime, end: datetime, reference) -> float:
    """Total volume in [start, end), regular session and same trading day.

    Bar timestamps are the bar's *start* (Alpaca's convention), so a 5-minute
    bar stamped 15:55 covers 15:55-16:00 and belongs to a window ending at
    16:00. Half-open on the right for that reason -- a bar stamped exactly
    at `end` starts the next window, not this one.
    """
    return sum(
        bar.volume
        for bar in bars
        if is_regular_session_bar(bar)
        and same_trading_day(bar, reference)
        and start <= bar.timestamp < end
    )


def _anchor(bars: list):
    """(reference_bar, window_end) or None -- the single place the "now"
    anchor and its half-open right edge are derived, so the two public
    entry points can't drift apart on the boundary.
    """
    reference = latest_session_bar(bars)
    if reference is None:
        return None
    # The anchor bar's own interval counts as part of the recent window, so
    # the window ends at the bar's end, not its start.
    return reference, reference.timestamp + _BAR_LENGTH


def trailing_windows(bars: list, window: timedelta = DEFAULT_WINDOW):
    """(recent, prior) volume for the two consecutive windows ending at the
    latest regular-session bar.

    Returns (None, None) when there's no session bar to anchor on -- outside
    the session there is no "right now" to measure, the same stance
    MomentumCache takes for the 15-minute momentum figure.
    """
    anchored = _anchor(bars)
    if anchored is None:
        return None, None
    reference, end = anchored
    recent = window_volume(bars, end - window, end, reference)
    prior = window_volume(bars, end - 2 * window, end - window, reference)
    return recent, prior


def surge_ratio(recent: float | None, prior: float | None) -> float | None:
    """Trailing window over the one before it. None when the prior window
    had no volume at all -- a ratio against zero is not "infinitely
    accelerating", it's unmeasurable, and reporting a huge number would put
    the thinnest symbols at the top of a sort.
    """
    if recent is None or not prior or prior <= 0:
        return None
    return recent / prior


def expected_window_volume(
    avg_vol_20d: float, curve: list, start: datetime, end: datetime
) -> float | None:
    """How much volume normally prints between `start` and `end`.

    The volume profile gives cumulative share-of-day at a time, so the share
    falling *inside* a window is the difference between its endpoints -- that
    subtraction is what removes the U-shape this is meant to control for.
    """
    if avg_vol_20d <= 0 or not curve:
        return None
    share = fraction_at(curve, end) - fraction_at(curve, start)
    if share <= 0:
        return None
    return avg_vol_20d * share


def windowed_rvol(
    bars: list, avg_vol_20d: float, curve: list, window: timedelta = DEFAULT_WINDOW
) -> float | None:
    """Trailing-window volume over what that clock window normally trades.

    Reads like RVOL and is directly comparable in spirit -- "2x" is twice
    normal -- but is a rate rather than a running total, so it can fall as
    well as rise and says something about now rather than about the whole
    day so far.
    """
    anchored = _anchor(bars)
    if anchored is None:
        return None
    reference, end = anchored
    start = end - window
    recent = window_volume(bars, start, end, reference)
    expected = expected_window_volume(avg_vol_20d, curve, start, end)
    if expected is None:
        return None
    return recent / expected
