from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.market_data.momentum import MOMENTUM_WINDOW_MINUTES
from app.routers.admin import require_admin
from app.scanners.benchmark_tracker import compute_performance

router = APIRouter(prefix="/api/scanners", tags=["scanners"])

SCANNER_PAUSED_KEY = "scanner_paused"


@router.get("/status")
async def get_scanner_status(request: Request) -> dict:
    """Whether the operator has paused market-wide scanning (see
    ScannerEngine.paused): the views then hold their last rows, no
    history snapshots are written and the API budget goes to the charts."""
    engine = request.app.state.scanner_engine
    return {"paused": bool(engine.paused), "session": engine.session}


class PauseRequest(BaseModel):
    paused: bool


@router.post("/pause")
async def set_scanner_pause(body: PauseRequest, request: Request, _admin: dict = Depends(require_admin)) -> dict:
    """Admins only: a server-wide switch, persisted so it survives a
    restart."""
    engine = request.app.state.scanner_engine
    engine.paused = body.paused
    kv = getattr(request.app.state, "kv_store", None)
    if kv is not None:
        await kv.set(SCANNER_PAUSED_KEY, body.paused)
    return {"paused": bool(engine.paused), "session": engine.session}


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
        # Today's headline as it stands now, reported *beside* the frozen
        # entry_headline rather than replacing it -- see compute_performance.
        lambda symbol: engine.news_cache.get(symbol) if engine.news_cache else None,
        # Read off the universe rather than engine.rows: a symbol flagged
        # earlier may have since dropped out of every ranked view, and the
        # universe entry is where the asset's shortable flag actually lives.
        lambda symbol: bool(uni.shortable) if (uni := engine.universe.get(symbol)) else False,
    )
    return {"benchmark_symbol": engine.benchmark_symbol, "picks": picks}


@router.get("/history/performance")
async def get_history_performance(
    request: Request, days: int = 7, view: str | None = None
) -> dict:
    """Aggregated performance of every scanner match logged to the
    persistent SQLite history store (see app.scanners.history_store) --
    unlike /benchmark-performance above, this survives backend restarts and
    covers everything flagged in the last `days` trading days, not just
    whatever's still resident in the in-memory tracker. Registered before
    GET /{name} for the same reason /benchmark-performance is: both are
    single-segment paths under /api/scanners.
    """
    store = request.app.state.scanner_history_store
    return await store.compute_performance(days=days, view=view)


@router.get("/{name}")
async def get_scanner(name: str, request: Request) -> dict:
    """One-shot fetch for initial page load, before the WebSocket connects."""
    engine = request.app.state.scanner_engine
    rows = engine.snapshot_view(name)
    return {
        "scanner": name,
        "session": engine.session,
        "is_latest_session": engine.is_latest_session_fallback,
        "window_minutes": engine.settings.scanner_volume_surge_window_minutes,
        "momentum_window_minutes": MOMENTUM_WINDOW_MINUTES,
        "rows": [r.model_dump(mode="json") for r in rows],
    }
