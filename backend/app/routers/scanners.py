from fastapi import APIRouter, Request

from app.scanners.benchmark_tracker import compute_performance

router = APIRouter(prefix="/api/scanners", tags=["scanners"])


@router.get("/benchmark-performance")
async def get_benchmark_performance(request: Request) -> dict:
    """How symbols the scanner itself first flagged (gainers/losers/most
    active, not just AI-curated picks -- see /api/trade-ideas/performance
    for that) have actually moved since, versus the broad market -- the
    real test of whether the scanner's own selection criteria find stocks
    that keep moving. Registered before GET /{name} below: both are single-
    segment paths under /api/scanners, so this more specific route has to
    come first or "/benchmark-performance" would get swallowed by {name}.
    """
    engine = request.app.state.scanner_engine
    tracker = request.app.state.scanner_benchmark_tracker
    picks = compute_performance(
        tracker.all(),
        lambda symbol: (row.last_price if (row := engine.rows.get(symbol)) else None),
        engine.benchmark_price,
    )
    return {"benchmark_symbol": engine.benchmark_symbol, "picks": picks}


@router.get("/{name}")
async def get_scanner(name: str, request: Request) -> dict:
    """One-shot fetch for initial page load, before the WebSocket connects."""
    engine = request.app.state.scanner_engine
    rows = engine.snapshot_view(name)
    return {
        "scanner": name,
        "session": engine.session,
        "is_latest_session": engine.is_latest_session_fallback,
        "rows": [r.model_dump(mode="json") for r in rows],
    }
