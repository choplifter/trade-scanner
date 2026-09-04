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

Every endpoint takes `user: dict = Depends(get_current_user)` -- Simulation
Mode is per-user (see app.trading.sim.store), so every call needs to know
whose account it's touching, not just that *someone* is logged in.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth.dependency import get_current_user
from app.options.occ import try_parse_occ
from app.trading.errors import TradingError
from app.trading.models import OrderTicket
from app.trading.sim.service import SimOrderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading/sim", tags=["trading-sim"])


async def _replay_seam(request: Request, user_id: int):
    """(ReplayEngine, as_of) whenever this user has *any* replay session --
    playing or paused -- with a resident engine; see
    SimOrderService.__init__. Gated on session existence, not `playing`:
    "playing" only controls whether the clock auto-advances, it says
    nothing about which prices are on screen. A paused session still shows
    the user historical rows at a frozen as_of, so an order placed while
    paused must price against that same frozen moment -- falling through
    to a live quote here would silently trade against a different price
    than the one the user is looking at.
    """
    replay_store = getattr(request.app.state, "replay_store", None)
    replay_engines = getattr(request.app.state, "replay_engines", None)
    if replay_store is None or replay_engines is None:
        return None
    session = await replay_store.get(user_id)
    if session is None:
        return None
    engine = replay_engines.get(user_id)
    if engine is None:
        return None
    return (engine, datetime.fromisoformat(session["as_of"]))


async def _service(request: Request, user: dict) -> SimOrderService:
    settings = request.app.state.settings
    return SimOrderService(
        request.app.state.alpaca_clients,
        settings,
        request.app.state.sim_store,
        user["id"],
        engine=getattr(request.app.state, "scanner_engine", None),
        replay=await _replay_seam(request, user["id"]),
    )


async def _options_service(request: Request, user: dict):
    """The simulated options book for this user, or None when it cannot
    price (no Alpaca data credentials) -- the equity endpoints below fold
    its positions in so the trading panel shows the whole account."""
    from app.trading.sim.options_service import make_sim_options_service

    state = request.app.state
    options_store = getattr(state, "sim_options_store", None)
    if options_store is None or not state.settings.has_credentials:
        return None
    return make_sim_options_service(
        state.alpaca_clients,
        state.settings,
        sim_store=state.sim_store,
        options_store=options_store,
        user_id=user["id"],
        seam=await _replay_seam(request, user["id"]),
        option_engines=getattr(state, "replay_option_engines", None),
        chain_cache=getattr(state, "options_chain_cache", None),
        engine=getattr(state, "scanner_engine", None),
    )


async def _option_marks(request: Request, user: dict) -> list[dict]:
    options = await _options_service(request, user)
    if options is None:
        return []
    try:
        return await options.marked_positions()
    except Exception:
        logger.exception("Sim option positions failed")
        return []


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@router.get("/account")
async def get_account(request: Request, user: dict = Depends(get_current_user)) -> dict:
    settings = request.app.state.settings
    try:
        account = await (await _service(request, user)).account()
    except Exception:
        logger.exception("Sim account fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read the simulated account") from None
    marks = await _option_marks(request, user)
    if marks:
        # Held contracts count toward equity like held shares do.
        value = sum(_num(p.get("market_value")) for p in marks)
        for key in ("equity", "last_equity", "portfolio_value"):
            account[key] = f"{_num(account.get(key)) + value:.2f}"
        longs = sum(_num(p.get("market_value")) for p in marks if _num(p.get("market_value")) > 0)
        shorts = sum(_num(p.get("market_value")) for p in marks if _num(p.get("market_value")) < 0)
        account["long_market_value"] = f"{_num(account.get('long_market_value')) + longs:.2f}"
        account["short_market_value"] = f"{_num(account.get('short_market_value')) + shorts:.2f}"
    return {
        "account": account,
        "paper": True,
        "trading_enabled": True,
        "default_risk_pct": settings.trading_default_risk_pct,
    }


@router.get("/positions")
async def get_positions(request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        positions = await (await _service(request, user)).positions()
    except Exception:
        logger.exception("Sim positions fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read simulated positions") from None
    # Held option contracts ride along, per contract, the way Alpaca lists
    # them -- so the Positions tab and the premium chart's ticket see them.
    return {"positions": [*positions, *await _option_marks(request, user)]}


@router.get("/orders")
async def get_orders(request: Request, status: str = "open", user: dict = Depends(get_current_user)) -> dict:
    try:
        orders = await (await _service(request, user)).orders(status)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim orders fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read simulated orders") from None
    return {"orders": orders, "status": status}


@router.get("/trades")
async def get_trades(
    request: Request, range_key: str = Query("all", alias="range"), user: dict = Depends(get_current_user)
) -> dict:
    try:
        return await (await _service(request, user)).trades(range_key)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim trades fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read simulated trades") from None


@router.get("/portfolio-history")
async def get_portfolio_history(
    request: Request, range_key: str = Query("1M", alias="range"), user: dict = Depends(get_current_user)
) -> dict:
    try:
        return await (await _service(request, user)).portfolio_history(range_key)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim portfolio history fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read simulated balance history") from None


@router.get("/reference-price/{symbol}")
async def get_reference_price(symbol: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        price = await (await _service(request, user)).reference_price(symbol.upper())
    except Exception:
        logger.exception("Sim reference-price lookup failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to fetch reference price") from None
    return {"price": price}


@router.get("/day-high/{symbol}")
async def get_day_high(symbol: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        high = await (await _service(request, user)).day_high(symbol.upper())
    except Exception:
        logger.exception("Sim day-high lookup failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to fetch day high") from None
    return {"day_high": high}


@router.post("/orders/preview")
async def preview_order(ticket: OrderTicket, request: Request, user: dict = Depends(get_current_user)) -> dict:
    settings = request.app.state.settings
    try:
        resolved = await (await _service(request, user)).preview(ticket)
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
async def submit_order(ticket: OrderTicket, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        order = await (await _service(request, user)).submit(ticket)
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
async def replace_stop(
    order_id: str, body: ReplaceStopRequest, request: Request, user: dict = Depends(get_current_user)
) -> dict:
    try:
        order = await (await _service(request, user)).replace_stop(order_id, body.symbol, body.stop_price)
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
async def replace_target(
    order_id: str, body: ReplaceTargetRequest, request: Request, user: dict = Depends(get_current_user)
) -> dict:
    try:
        order = await (await _service(request, user)).replace_target(order_id, body.symbol, body.limit_price)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim target replace failed for %s", order_id)
        raise HTTPException(status_code=502, detail="Failed to move the simulated target") from None
    return {"order": order}


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        await (await _service(request, user)).cancel(order_id)
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
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        if try_parse_occ(symbol.upper()) is not None:
            options = await _options_service(request, user)
            if options is None:
                raise HTTPException(status_code=503, detail="Alpaca credentials not configured")
            order = await options.close_contract(symbol, int(qty) if qty is not None else None)
        else:
            order = await (await _service(request, user)).close_position(symbol, qty)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Sim close position failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to close the simulated position") from None
    return {"order": order}


@router.post("/reset")
async def reset_sim_account(request: Request, user: dict = Depends(get_current_user)) -> dict:
    """Wipes simulated positions/orders/trades and reseeds cash back to
    settings.trading_sim_starting_cash. No real-trading equivalent --
    practice is meant to be restartable."""
    settings = request.app.state.settings
    try:
        options_store = getattr(request.app.state, "sim_options_store", None)
        if options_store is not None:
            await options_store.reset(user["id"])
        account = await (await _service(request, user)).reset()
    except Exception:
        logger.exception("Sim account reset failed")
        raise HTTPException(status_code=502, detail="Failed to reset the simulated account") from None
    return {
        "account": account,
        "paper": True,
        "trading_enabled": True,
        "default_risk_pct": settings.trading_default_risk_pct,
    }
