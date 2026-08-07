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
