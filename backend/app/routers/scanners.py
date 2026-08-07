from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/scanners", tags=["scanners"])


@router.get("/{name}")
async def get_scanner(name: str, request: Request) -> dict:
    """One-shot fetch for initial page load, before the WebSocket connects."""
    engine = request.app.state.scanner_engine
    rows = engine.snapshot_view(name)
    return {
        "scanner": name,
        "session": engine.session,
        "rows": [r.model_dump(mode="json") for r in rows],
    }
