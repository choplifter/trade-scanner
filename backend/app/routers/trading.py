"""Trading API -- account state now, order placement behind a switch.

Read endpoints are always available when credentials exist: showing an
account balance is harmless. The write endpoints land in a later milestone
and are gated in app.trading.service, not here, so the guard holds for every
caller rather than every route.

Structured 422 bodies match app.routers.screener's refusal shape ({code,
message, field}), because the frontend already knows how to read a typed
detail off a 422 -- see BacktestRefusedError in frontend/src/api/http.ts.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.trading.errors import TradingError
from app.trading.service import OrderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading"])


def _service(request: Request) -> OrderService:
    settings = request.app.state.settings
    if not settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")
    return OrderService(request.app.state.alpaca_clients, settings)


@router.get("/account")
async def get_account(request: Request) -> dict:
    """Buying power, equity and cash for the connected account.

    Also reports which mode the app is in, so the UI can label itself rather
    than the user having to remember what is in their .env -- the difference
    between simulated and real is not something to leave implicit.
    """
    settings = request.app.state.settings
    try:
        account = await _service(request).account()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Alpaca account fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    return {
        "account": account,
        "paper": settings.alpaca_paper,
        "trading_enabled": settings.trading_enabled,
    }


@router.get("/positions")
async def get_positions(request: Request) -> dict:
    try:
        positions = await _service(request).positions()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Alpaca positions fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    return {"positions": positions}


@router.get("/orders")
async def get_orders(request: Request, status: str = "open") -> dict:
    """Working orders by default; pass status=all or closed for history."""
    try:
        orders = await _service(request).orders(status)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Alpaca orders fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    return {"orders": orders, "status": status}
