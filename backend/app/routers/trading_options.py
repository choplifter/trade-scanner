"""Options spreads: chain, preview, submit, open spreads, close, and the
underlying-price triggers. Mounted twice like the equity trading router --
/api/trading/options for the paper account and /api/trading/live/options
for the real one (see app/main.py and app.routers.trading.mark_live_account).

Same error convention as app.routers.trading: a TradingError is a 422 with
{code, message, field}; anything else is a 502.
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.dependency import get_current_user
from app.options.models import CloseSpreadRequest, SpreadTicket, TriggerCreate
from app.options.service import OptionsService
from app.routers.trading import _account, _confirm
from app.trading.errors import TradingError
from app.trading.guards import can_submit, limits_for

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trading-options"])


def _service(request: Request) -> OptionsService:
    settings = request.app.state.settings
    if not settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")
    account = _account(request)
    if account == "live" and not settings.has_live_credentials:
        raise HTTPException(status_code=503, detail="Live account not configured")
    return OptionsService(
        request.app.state.alpaca_clients,
        settings,
        engine=getattr(request.app.state, "scanner_engine", None),
        chain_cache=getattr(request.app.state, "options_chain_cache", None),
        account=account,
    )


def _limits(request: Request) -> dict:
    settings = request.app.state.settings
    limits = limits_for(settings, _account(request))
    return {
        "account": limits.account,
        "max_contracts": limits.max_option_contracts,
        "max_order_notional": limits.max_order_notional,
    }


@router.get("/account")
async def options_account(request: Request) -> dict:
    settings = request.app.state.settings
    try:
        account = await _service(request).account()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Options account fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    return {
        **account,
        "can_submit": can_submit(settings, _account(request)),
        "feed": settings.alpaca_options_feed,
        "limits": _limits(request),
    }


@router.get("/expiries/{underlying}")
async def expiries(underlying: str, request: Request) -> dict:
    try:
        return await _service(request).expiries(underlying.upper())
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Option expiries failed for %s", underlying)
        raise HTTPException(status_code=502, detail="Failed to load option expiries")


@router.get("/chain/{underlying}")
async def chain(
    underlying: str,
    request: Request,
    expiry: date = Query(..., description="Expiration date, YYYY-MM-DD"),
) -> dict:
    try:
        return (await _service(request).chain(underlying.upper(), expiry)).to_dict()
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Option chain failed for %s %s", underlying, expiry)
        raise HTTPException(status_code=502, detail="Failed to load the option chain")


@router.post("/preview")
async def preview_spread(ticket: SpreadTicket, request: Request) -> dict:
    settings = request.app.state.settings
    try:
        resolved = await _service(request).preview(ticket)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Spread preview failed for %s", ticket.underlying)
        raise HTTPException(status_code=502, detail="Failed to price the spread")
    return {
        "spread": resolved.model_dump(mode="json"),
        "can_submit": can_submit(settings, _account(request)),
        "limits": _limits(request),
    }


@router.post("/orders")
async def submit_spread(ticket: SpreadTicket, request: Request) -> dict:
    try:
        order = await _service(request).submit(ticket, confirm=_confirm(request))
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Spread submission failed for %s", ticket.underlying)
        raise HTTPException(status_code=502, detail="Failed to submit the spread")
    return {"order": order}


@router.get("/spreads")
async def spreads(request: Request, user: dict = Depends(get_current_user)) -> dict:
    store = getattr(request.app.state, "options_trigger_store", None)
    try:
        groups = await _service(request).spreads()
        triggers = await store.list_for_user(user["id"], _account(request)) if store is not None else []
    except HTTPException:
        raise
    except Exception:
        logger.exception("Open spreads fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    return {"spreads": [g.to_dict() for g in groups], "triggers": triggers}


@router.post("/spreads/close/preview")
async def preview_close(body: CloseSpreadRequest, request: Request) -> dict:
    try:
        return await _service(request).preview_close(body)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Close preview failed")
        raise HTTPException(status_code=502, detail="Failed to price the close")


@router.post("/spreads/close")
async def close_spread(body: CloseSpreadRequest, request: Request) -> dict:
    try:
        order = await _service(request).close_spread(body, confirm=_confirm(request))
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Spread close failed")
        raise HTTPException(status_code=502, detail="Failed to submit the closing order")
    return {"order": order}


# --- underlying-price triggers --------------------------------------------------


def _trigger_store(request: Request):
    store = getattr(request.app.state, "options_trigger_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Trigger store not initialised")
    return store


@router.get("/triggers")
async def list_triggers(request: Request, user: dict = Depends(get_current_user)) -> dict:
    return {"triggers": await _trigger_store(request).list_for_user(user["id"], _account(request))}


@router.post("/triggers")
async def create_trigger(body: TriggerCreate, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """Arm a stop and/or target on the underlying's price and/or on the
    position's own premium (the mark of the closing package). Arming a live
    trigger is itself a real-money decision, so it asks for the typed
    confirmation; the loop that later fires it does not ask again."""
    from app.trading.guards import assert_can_trade

    settings = request.app.state.settings
    account = _account(request)
    try:
        assert_can_trade(settings, account, _confirm(request))
        service = _service(request)
        spot = await service.spot(body.underlying.upper())
        if spot is not None:
            if body.close_below is not None and body.close_below >= spot:
                raise TradingError(
                    f"close_below {body.close_below:g} is not below the current price {spot:.2f}",
                    field="close_below",
                )
            if body.close_above is not None and body.close_above <= spot:
                raise TradingError(
                    f"close_above {body.close_above:g} is not above the current price {spot:.2f}",
                    field="close_above",
                )
        trigger = await _trigger_store(request).create(user["id"], account, body)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Trigger create failed")
        raise HTTPException(status_code=502, detail="Failed to arm the trigger")
    return {"trigger": trigger}


@router.delete("/triggers/{trigger_id}")
async def cancel_trigger(trigger_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    if not await _trigger_store(request).cancel(user["id"], trigger_id):
        raise HTTPException(status_code=404, detail="No such trigger")
    return {"cancelled": trigger_id}
