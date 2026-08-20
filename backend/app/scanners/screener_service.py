"""The engine-coupled half of the screener.

app.scanners.screener is deliberately pure -- filters, sorting and the field
registry, with no knowledge of the running engine, so it unit-tests without
any of it. But two screenable fields can only be produced from live engine
state, and both front ends need them computed identically. This module is
that seam: the HTTP router and the Dash page both call screen_live_rows,
rather than each assembling derived values for itself and drifting.
"""

from datetime import timedelta

from app.market_data import volume_surge
from app.scanners import formulas, screener
from app.scanners.engine import _has_headline, _tradable

# One 5-minute bar is the smallest window that can hold anything, and a
# window under two bars is dominated by whichever single print landed in it.
_MIN_WINDOW_MINUTES = 5


def derived_values(engine, rows, window_minutes: int | None = None) -> dict[str, dict]:
    """Values for the fields that aren't attributes of ScannerRow.

    float_shares is read from FundamentalsCache's universe-wide bulk map
    rather than row.float_shares: the cache holds float for every symbol
    (~8 requests for ~23k, see fundamentals.float_bulk) while the row
    attribute is only filled in for symbols already in a ranked view. Going
    to the cache is what makes float screenable across the whole universe
    without widening any fetch.

    rank_score is recomputed rather than stored, so a screen can order by
    exactly what the fixed views ordered by -- catalyst boost and fade-risk
    discount included. catalyst_boost follows pct_change's sign because the
    boost is gainers-only (see formulas._CATALYST_BOOST); a screen holding
    both directions at once therefore gives each side the same treatment its
    own live view would.

    rvol_window is the reason this function takes a window at all. The row
    already carries rvol_1h, but that one is fixed to the global setting;
    a screen asking for 30 minutes needs the ratio recomputed. It costs no
    fetch -- MomentumCache.bars hands back the same 5-minute series rvol_1h
    was built from, so this is arithmetic over data already in memory.

    volume_concentration divides it by the cumulative rvol on the row.
    Deliberately not its own formula: "is right now hotter than the rest of
    today" is exactly that quotient, and deriving it here keeps the two
    definitions from drifting.
    """
    fundamentals = engine.fundamentals
    news_cache = engine.news_cache
    momentum = engine.momentum_cache
    minutes = window_minutes or engine.settings.scanner_volume_surge_window_minutes
    window = timedelta(minutes=minutes)
    curve = engine.volume_profile.curve

    rvol_window: dict[str, float | None] = {}
    concentration: dict[str, float | None] = {}
    for r in rows:
        value = volume_surge.windowed_rvol(momentum.bars(r.symbol), r.avg_vol_20d, curve, window)
        rvol_window[r.symbol] = value
        # r.rvol is 0.0 rather than None when unavailable (see the `or 0.0`
        # in the engine), so guard on falsiness, not on None.
        concentration[r.symbol] = value / r.rvol if value is not None and r.rvol else None

    return {
        "rvol_window": rvol_window,
        "volume_concentration": concentration,
        "is_green_candle": {r.symbol: momentum.is_green(r.symbol) for r in rows},
        "float_shares": {r.symbol: fundamentals.float_shares(r.symbol) for r in rows},
        "short_interest_pct": {
            r.symbol: fundamentals.short_interest_pct(r.symbol) for r in rows
        },
        "rank_score": {
            r.symbol: formulas.rank_score(
                r.pct_change,
                _has_headline(r, news_cache),
                r.rvol,
                catalyst_boost=r.pct_change > 0,
            )
            for r in rows
        },
    }


def run_live_screen(engine, settings, screen: screener.Screen):
    """Run `screen` against whatever the engine currently holds, returning
    (matched_rows, meta) with the rows still as ScannerRow *objects*.

    Split from screen_live_rows so the poll loop can enrich those rows --
    fundamentals, news, momentum -- before anything is serialized. They're
    shared objects, so enriching them there is what fills those columns in
    the payload sent to a live screen subscriber.

    Applies the same tradability floor the ranked views apply
    (engine._tradable with settings.scanner_min_dollar_volume) *before* any
    user filter, so a screen can't surface rows the scanner itself would
    never show. Falls back to the most recently completed session's rows
    when nothing is live, exactly as the fixed views do -- an empty screener
    while the market is closed would look broken rather than idle.
    """
    source = list(engine.rows.values()) or list((engine._latest_session_rows or {}).values())
    rows = _tradable(source, settings.scanner_min_dollar_volume)
    # Floored rather than validated: the filter bar sends this on every
    # keystroke, so a half-typed "3" must produce a small window, not a 422
    # that blanks the user's screen mid-edit.
    minutes = max(_MIN_WINDOW_MINUTES, screen.window_minutes or settings.scanner_volume_surge_window_minutes)
    derived = derived_values(engine, rows, minutes)
    result = screener.run_screen(rows, screen, derived)

    meta = {
        "session": engine.session,
        # Echoed back resolved, never as the possibly-None value the screen
        # was submitted with: the client labels its column from this, and
        # "RVol (null m)" is not a header.
        "window_minutes": minutes,
        "is_latest_session": engine.is_latest_session_fallback,
        "total_matched": result.total_matched,
        "tradable_size": len(rows),
        "universe_size": len(source),
        # Derived columns travel beside the rows rather than on them: they
        # aren't ScannerRow fields, and folding them in would make the row
        # schema depend on which screen happened to run.
        "derived": result.values,
    }
    return result.rows, meta


def serialize_screen(rows, meta: dict) -> dict:
    return {**meta, "rows": [r.model_dump(mode="json") for r in rows]}


def screen_live_rows(engine, settings, screen: screener.Screen) -> dict:
    """One-shot screen for the HTTP router and the Dash page, which have no
    poll loop to enrich rows in and so serialize immediately.
    """
    rows, meta = run_live_screen(engine, settings, screen)
    return serialize_screen(rows, meta)
