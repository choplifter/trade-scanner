"""The engine-coupled half of the screener.

app.scanners.screener is deliberately pure -- filters, sorting and the field
registry, with no knowledge of the running engine, so it unit-tests without
any of it. But two screenable fields can only be produced from live engine
state, and both front ends need them computed identically. This module is
that seam: the HTTP router and the Dash page both call screen_live_rows,
rather than each assembling derived values for itself and drifting.
"""

from app.scanners import formulas, screener
from app.scanners.engine import _has_headline, _tradable


def derived_values(engine, rows) -> dict[str, dict]:
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
    """
    fundamentals = engine.fundamentals
    news_cache = engine.news_cache
    return {
        "float_shares": {r.symbol: fundamentals.float_shares(r.symbol) for r in rows},
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


def screen_live_rows(engine, settings, screen: screener.Screen) -> dict:
    """Run `screen` against whatever the engine currently holds.

    Applies the same tradability floor the ranked views apply
    (engine._tradable with settings.scanner_min_dollar_volume) *before* any
    user filter, so a screen can't surface rows the scanner itself would
    never show. Falls back to the most recently completed session's rows
    when nothing is live, exactly as the fixed views do -- an empty screener
    while the market is closed would look broken rather than idle.
    """
    source = list(engine.rows.values()) or list((engine._latest_session_rows or {}).values())
    rows = _tradable(source, settings.scanner_min_dollar_volume)
    derived = derived_values(engine, rows)
    result = screener.run_screen(rows, screen, derived)

    return {
        "session": engine.session,
        "is_latest_session": engine.is_latest_session_fallback,
        "total_matched": result.total_matched,
        "tradable_size": len(rows),
        "universe_size": len(source),
        "rows": [r.model_dump(mode="json") for r in result.rows],
        # Derived columns travel beside the rows rather than on them: they
        # aren't ScannerRow fields, and folding them in would make the row
        # schema depend on which screen happened to run.
        "derived": result.values,
    }
