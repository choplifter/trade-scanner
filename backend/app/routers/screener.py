"""Screener API -- shared by both front ends.

The React app and the Dash app both build their filter controls and column
pickers from GET /fields rather than listing columns themselves, so a field
added to app.scanners.screener.FIELDS shows up in both with no UI change.
That is the whole reason the registry lives on the server.
"""

from fastapi import APIRouter, Request

from app.scanners import formulas, screener
from app.scanners.engine import _has_headline, _tradable

router = APIRouter(prefix="/api/screener", tags=["screener"])


def _derived_values(request: Request, rows) -> dict[str, dict]:
    """Values for the fields that aren't on ScannerRow itself.

    float_shares comes from the universe-wide bulk file rather than the row:
    FundamentalsCache holds float for every symbol (~8 requests for ~23k
    symbols, see fundamentals.float_bulk), while row.float_shares is only
    filled in for symbols already in a ranked view. Reading the cache here is
    what makes float screenable across the whole universe without widening
    any fetch.

    rank_score is recomputed rather than stored so a screen orders by exactly
    what the live views order by, catalyst boost and fade-risk discount
    included -- see formulas.rank_score. It needs the news cache, which is why
    it can't live in the pure screener module.
    """
    engine = request.app.state.scanner_engine
    fundamentals = engine.fundamentals
    news_cache = engine.news_cache
    return {
        "float_shares": {r.symbol: fundamentals.float_shares(r.symbol) for r in rows},
        "rank_score": {
            r.symbol: formulas.rank_score(
                r.pct_change, _has_headline(r, news_cache), r.rvol,
                # Gainers-only, matching engine._rank_gainers/_rank_losers.
                # A screen sorting by rank_score on a mixed set gets the same
                # treatment the live views give each side.
                catalyst_boost=r.pct_change > 0,
            )
            for r in rows
        },
    }


@router.get("/fields")
async def get_fields() -> dict:
    """The screenable field registry: name, label, type and the operators
    valid for that type. Clients render their UI from this.
    """
    return {"fields": screener.fields_payload()}


@router.get("/presets")
async def get_presets() -> dict:
    """Built-in screens, including the three that used to be hardcoded
    views. Returned as full filter specs so a UI can load one and then let
    the user edit it, rather than treating them as opaque names.
    """
    return {"presets": screener.preset_list()}


@router.post("/run")
async def run_screen(screen: screener.Screen, request: Request) -> dict:
    """Run a screen against the current live rows.

    Applies the same tradability floor the ranked views apply
    (engine._tradable with settings.scanner_min_dollar_volume) before any
    user filter, so a screen can't surface rows the scanner itself would
    never show. Falls back to the most recently completed session's rows
    when nothing is live, exactly as the fixed views do.
    """
    engine = request.app.state.scanner_engine
    settings = request.app.state.settings

    source = engine.rows.values() or (engine._latest_session_rows or {}).values()
    rows = _tradable(source, settings.scanner_min_dollar_volume)
    derived = _derived_values(request, rows)
    result = screener.run_screen(rows, screen, derived)

    return {
        "session": engine.session,
        "is_latest_session": engine.is_latest_session_fallback,
        "total_matched": result.total_matched,
        "universe_size": len(engine.rows) or len(engine._latest_session_rows or {}),
        "rows": [r.model_dump(mode="json") for r in result.rows],
        # Derived columns travel beside the rows rather than on them: they
        # aren't ScannerRow fields, and stuffing them in would make the row
        # schema depend on which screen ran.
        "derived": result.values,
    }
