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

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.trading.errors import TradingError
from app.trading.models import OrderTicket
from app.trading.service import OrderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading"])


def _service(request: Request) -> OrderService:
    settings = request.app.state.settings
    if not settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")
    return OrderService(
        request.app.state.alpaca_clients,
        settings,
        engine=getattr(request.app.state, "scanner_engine", None),
    )


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
        # Prefills the ticket, so changing the setting is reflected in the UI
        # rather than the two drifting apart.
        "default_risk_pct": settings.trading_default_risk_pct,
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


@router.get("/trades")
async def get_trades(request: Request) -> dict:
    """Closed round trips with realized P&L, newest first, plus a summary.

    Read-only like the other history routes, and each call also records
    any newly closed trip -- see OrderService.sync_trades for why the
    store, not the broker, is the record.
    """
    store = getattr(request.app.state, "trade_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Trade record not initialised")
    try:
        return await _service(request).sync_trades(store)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Trade sync failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")


@router.get("/portfolio-history")
async def get_portfolio_history(
    request: Request,
    # Aliased so the query string reads ?range=1M while the parameter avoids
    # shadowing the builtin. Which ranges exist is the service's business --
    # the period/timeframe pairing is constrained by Alpaca, so an unknown
    # one is refused there and arrives here as the usual typed 422.
    range_key: str = Query("1M", alias="range"),
) -> dict:
    """The account equity curve, for the balance chart.

    A read path like account/positions/orders, and ungated for the same
    reason: looking at a balance is harmless whichever account it is.
    """
    try:
        return await _service(request).portfolio_history(range_key)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Alpaca portfolio history fetch failed for range %s", range_key)
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")


@router.post("/orders/preview")
async def preview_order(ticket: OrderTicket, request: Request) -> dict:
    """Size and price a ticket without placing anything.

    Ungated on purpose: this is arithmetic, and seeing the size and risk of
    an order you are not permitted to place is useful rather than dangerous.
    It is also what lets the ticket show a rejection *before* the user
    commits to anything.

    The limits travel with the response so the UI can explain a refusal in
    terms of the ceiling that caused it rather than restating a number.
    """
    settings = request.app.state.settings
    try:
        resolved = await _service(request).preview(ticket)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Order preview failed for %s", ticket.symbol)
        raise HTTPException(status_code=502, detail="Failed to price the order")

    return {
        "order": resolved.model_dump(mode="json"),
        "can_submit": settings.trading_enabled and settings.alpaca_paper,
        "limits": {
            "max_order_qty": settings.trading_max_order_qty,
            "max_order_notional": settings.trading_max_order_notional,
            "default_risk_pct": settings.trading_default_risk_pct,
        },
    }


@router.post("/orders")
async def submit_order(ticket: OrderTicket, request: Request) -> dict:
    """Place an order.

    Every refusal -- switched off, live account, bad stop, past a ceiling,
    or the broker's own -- arrives as a 422 with the same structured body
    the preview uses, so the ticket renders them all through one path.
    """
    try:
        order = await _service(request).submit(ticket)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Order submission failed for %s", ticket.symbol)
        raise HTTPException(status_code=502, detail="Failed to submit the order")
    return {"order": order}


class ReplaceStopRequest(BaseModel):
    """Body of the stop-move endpoint. The symbol rides along purely as a
    cross-check: the client names the order it means AND the position it
    thinks that order protects, and the service refuses if they disagree."""

    symbol: str = Field(min_length=1)
    stop_price: float


@router.patch("/orders/{order_id}")
async def replace_stop(order_id: str, body: ReplaceStopRequest, request: Request) -> dict:
    """Move a working stop -- the edited SL cell and the break-even button."""
    try:
        order = await _service(request).replace_stop(order_id, body.symbol, body.stop_price)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Stop replace failed for %s", order_id)
        raise HTTPException(status_code=502, detail="Failed to move the stop")
    return {"order": order}


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, request: Request) -> dict:
    try:
        await _service(request).cancel(order_id)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Order cancel failed for %s", order_id)
        raise HTTPException(status_code=502, detail="Failed to cancel the order")
    return {"cancelled": order_id}


@router.delete("/positions/{symbol}")
async def close_position(
    symbol: str,
    request: Request,
    qty: float | None = Query(
        default=None,
        gt=0,
        description=(
            "Shares to sell. Omitted flattens the whole position; given, the "
            "position's working exits are re-armed for what remains -- see "
            "OrderService.close_position."
        ),
    ),
) -> dict:
    """Flatten one position, or sell part of it. There is deliberately no
    close-all endpoint."""
    try:
        order = await _service(request).close_position(symbol, qty)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Close position failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to close the position")
    return {"order": order}
