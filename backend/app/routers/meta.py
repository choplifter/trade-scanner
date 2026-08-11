from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.services.market_clock import current_session

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "has_alpaca_credentials": settings.has_credentials,
        "has_anthropic_credentials": settings.has_anthropic_credentials,
        "data_feed": settings.alpaca_data_feed,
        "universe_size": len(request.app.state.universe),
    }


@router.get("/session")
async def session(_: Request) -> dict:
    return {
        "session": current_session(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/market-conditions")
async def market_conditions(request: Request) -> dict:
    """Red/yellow/green market-conditions readout -- see
    app.market_data.market_conditions. `available: false` (not an error)
    when FMP_API_KEY isn't configured or the first refresh hasn't
    completed yet, same "missing just means not available right now"
    framing as every other FMP-dependent feature in this app.
    """
    engine = request.app.state.scanner_engine
    conditions = engine.market_conditions if engine is not None else None
    if conditions is None:
        return {"available": False}
    return {
        "available": True,
        "level": conditions.level,
        "reasons": conditions.reasons,
        "vix": (
            {"price": conditions.vix.price, "change_pct": conditions.vix.change_pct}
            if conditions.vix
            else None
        ),
        "high_impact_events_today": [
            {
                "date": e.date.isoformat(),
                "country": e.country,
                "event": e.event,
                "impact": e.impact,
            }
            for e in conditions.high_impact_events_today
        ],
        "breadth_pct": conditions.breadth_pct,
    }
