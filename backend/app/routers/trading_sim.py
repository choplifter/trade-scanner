"""Simulation Mode's trading API -- a twin of routers/trading.py's endpoint
set, served from a fully local order book (app.trading.sim) instead of
Alpaca's broker client. Deliberately a separate router rather than a shared
factory over trading.py: the mapping from route to service is thin glue
(a try/except TradingError -> HTTPException wrapper, same shape throughout),
so duplicating it here costs little and keeps the tested real-trading
router untouched.

Unlike trading.py's _service(), this does not 503 on missing Alpaca
credentials -- account/positions/orders/trades/cancel/close/reset all work
with none configured; only the price-dependent calls (preview, submit,
reference-price, day-high, replace-*) degrade, through resolve_ticket's own
"no current price available" rejection.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.trading.errors import TradingError
from app.trading.models import OrderTicket
from app.trading.sim.service import SimOrderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading/sim", tags=["trading-sim"])


def _service(request: Request) -> SimOrderService:
    settings = request.app.state.settings
    return SimOrderService(
        request.app.state.alpaca_clients,
        settings,
        request.app.state.sim_store,
        engine=getattr(request.app.state, "scanner_engine", None),
    )


@router.get("/account")
async def get_account(request: Request) -> dict:
    settings = request.app.state.settings
    try:
        account = await _service(request).account()
    except Exception:
        logger.exception("Sim account fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read the simulated account") from None
    return {
        "account": account,
        "paper": True,
        "trading_enabled": True,
        "default_risk_pct": settings.trading_default_risk_pct,
    }


@router.get("/positions")
async def get_positions(request: Request) -> dict:
    try:
        positions = await _service(request).positions()
    except Exception:
        logger.exception("Sim positions fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read simulated positions") from None
    return {"positions": positions}


@router.get("/orders")
async def get_orders(request: Request, status: str = "open") -> dict:
    try:
        orders = await _service(request).orders(status)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim orders fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read simulated orders") from None
    return {"orders": orders, "status": status}


@router.get("/trades")
async def get_trades(request: Request, range_key: str = Query("all", alias="range")) -> dict:
    try:
        return await _service(request).trades(range_key)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim trades fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read simulated trades") from None


@router.get("/portfolio-history")
async def get_portfolio_history(request: Request, range_key: str = Query("1M", alias="range")) -> dict:
    try:
        return await _service(request).portfolio_history(range_key)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim portfolio history fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read simulated balance history") from None


@router.get("/reference-price/{symbol}")
async def get_reference_price(symbol: str, request: Request) -> dict:
    try:
        price = await _service(request).reference_price(symbol.upper())
    except Exception:
        logger.exception("Sim reference-price lookup failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to fetch reference price") from None
    return {"price": price}


@router.get("/day-high/{symbol}")
async def get_day_high(symbol: str, request: Request) -> dict:
    try:
        high = await _service(request).day_high(symbol.upper())
    except Exception:
        logger.exception("Sim day-high lookup failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to fetch day high") from None
    return {"day_high": high}


@router.post("/orders/preview")
async def preview_order(ticket: OrderTicket, request: Request) -> dict:
    settings = request.app.state.settings
    try:
        resolved = await _service(request).preview(ticket)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim order preview failed for %s", ticket.symbol)
        raise HTTPException(status_code=502, detail="Failed to price the order") from None

    return {
        "order": resolved.model_dump(mode="json"),
        # Simulation never has anything to refuse a submit on the grounds
        # trading_enabled/alpaca_paper exist for -- it never reaches a
        # broker. Always true, unlike the real endpoint's gated answer.
        "can_submit": True,
        "limits": {
            "max_order_qty": settings.trading_max_order_qty,
            "max_order_notional": settings.trading_max_order_notional,
            "default_risk_pct": settings.trading_default_risk_pct,
        },
    }


@router.post("/orders")
async def submit_order(ticket: OrderTicket, request: Request) -> dict:
    try:
        order = await _service(request).submit(ticket)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim order submission failed for %s", ticket.symbol)
        raise HTTPException(status_code=502, detail="Failed to submit the simulated order") from None
    return {"order": order}


class ReplaceStopRequest(BaseModel):
    symbol: str = Field(min_length=1)
    stop_price: float


@router.patch("/orders/{order_id}")
async def replace_stop(order_id: str, body: ReplaceStopRequest, request: Request) -> dict:
    try:
        order = await _service(request).replace_stop(order_id, body.symbol, body.stop_price)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim stop replace failed for %s", order_id)
        raise HTTPException(status_code=502, detail="Failed to move the simulated stop") from None
    return {"order": order}


class ReplaceTargetRequest(BaseModel):
    symbol: str = Field(min_length=1)
    limit_price: float


@router.patch("/orders/{order_id}/target")
async def replace_target(order_id: str, body: ReplaceTargetRequest, request: Request) -> dict:
    try:
        order = await _service(request).replace_target(order_id, body.symbol, body.limit_price)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim target replace failed for %s", order_id)
        raise HTTPException(status_code=502, detail="Failed to move the simulated target") from None
    return {"order": order}


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, request: Request) -> dict:
    try:
        await _service(request).cancel(order_id)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim order cancel failed for %s", order_id)
        raise HTTPException(status_code=502, detail="Failed to cancel the simulated order") from None
    return {"cancelled": order_id}


@router.delete("/positions/{symbol}")
async def close_position(
    symbol: str,
    request: Request,
    qty: float | None = Query(default=None, gt=0),
) -> dict:
    try:
        order = await _service(request).close_position(symbol, qty)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim close position failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to close the simulated position") from None
    return {"order": order}


@router.post("/reset")
async def reset_sim_account(request: Request) -> dict:
    """Wipes simulated positions/orders/trades and reseeds cash back to
    settings.trading_sim_starting_cash. No real-trading equivalent --
    practice is meant to be restartable."""
    settings = request.app.state.settings
    try:
        account = await _service(request).reset()
    except Exception:
        logger.exception("Sim account reset failed")
        raise HTTPException(status_code=502, detail="Failed to reset the simulated account") from None
    return {
        "account": account,
        "paper": True,
        "trading_enabled": True,
        "default_risk_pct": settings.trading_default_risk_pct,
    }
