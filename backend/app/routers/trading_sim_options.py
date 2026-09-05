"""Options over the simulated book -- the twin of routers/trading_options.py
under /api/trading/sim/options, the way routers/trading_sim.py twins the
equity router. Same endpoints, same error convention (a TradingError is
a 422 with {code, message, field}, anything else a 502), plus the two
the local book needs and Alpaca's does not: the resting packages and
their cancel.

Needs Alpaca's data credentials (the chain, live snapshots) but never
its broker: nothing here can place a real order, so there is no
TRADING_ENABLED gate and no typed confirmation. Per user like the rest of
Simulation Mode; while the user is in a history replay, prices come from
the replayed moment (see routers/trading_sim.py's _replay_seam).
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.ai.options_suggest import suggest_options_ideas
from app.auth.dependency import get_current_user
from app.options.models import CloseSpreadRequest, PayoffRequest, SpreadTicket, TriggerCreate
from app.options.occ import try_parse_occ
from app.routers.trading_options import IdeaRequest
from app.routers.trading_sim import _replay_seam
from app.trading.errors import TradingError
from app.trading.guards import limits_for
from app.trading.sim.options_service import SIM_ACCOUNT, SimOptionsService, make_sim_options_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading/sim/options", tags=["trading-sim-options"])


async def _service(request: Request, user: dict) -> SimOptionsService:
    state = request.app.state
    settings = state.settings
    if not settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")
    return make_sim_options_service(
        state.alpaca_clients,
        settings,
        sim_store=state.sim_store,
        options_store=state.sim_options_store,
        user_id=user["id"],
        seam=await _replay_seam(request, user["id"]),
        option_engines=getattr(state, "replay_option_engines", None),
        chain_cache=getattr(state, "options_chain_cache", None),
        engine=getattr(state, "scanner_engine", None),
    )


def _limits(request: Request) -> dict:
    limits = limits_for(request.app.state.settings, "paper")
    return {
        "account": SIM_ACCOUNT,
        "max_contracts": limits.max_option_contracts,
        "max_order_notional": limits.max_order_notional,
    }


def _trigger_store(request: Request):
    store = getattr(request.app.state, "options_trigger_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Trigger store not initialised")
    return store


@router.get("/account")
async def options_account(request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        account = await (await _service(request, user)).account()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim options account failed")
        raise HTTPException(status_code=502, detail="Failed to read the simulated options account")
    return {**account, "can_submit": True, "limits": _limits(request)}


@router.get("/expiries/{underlying}")
async def expiries(underlying: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        return await (await _service(request, user)).expiries(underlying.upper())
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim option expiries failed for %s", underlying)
        raise HTTPException(status_code=502, detail="Failed to load option expiries")


@router.get("/contract/{symbol}")
async def contract_quote(symbol: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """One contract at the book's moment: bid/ask/mid, greeks, IV."""
    symbol = symbol.upper()
    if try_parse_occ(symbol) is None:
        raise HTTPException(status_code=422, detail=f"Not an option contract symbol: {symbol}")
    try:
        quotes = await (await _service(request, user)).source.leg_quotes([symbol])
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim contract quote failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to load the contract quote")
    quote = quotes.get(symbol)
    if quote is None:
        raise HTTPException(status_code=404, detail=f"No quote for {symbol}")
    return quote.to_dict()


@router.get("/chain/{underlying}")
async def chain(
    underlying: str,
    request: Request,
    expiry: date = Query(..., description="Expiration date, YYYY-MM-DD"),
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        return (await (await _service(request, user)).chain(underlying.upper(), expiry)).to_dict()
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim option chain failed for %s %s", underlying, expiry)
        raise HTTPException(status_code=502, detail="Failed to load the option chain")


@router.post("/preview")
async def preview_spread(ticket: SpreadTicket, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        resolved = await (await _service(request, user)).preview(ticket)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim spread preview failed for %s", ticket.underlying)
        raise HTTPException(status_code=502, detail="Failed to price the spread")
    return {"spread": resolved.model_dump(mode="json"), "can_submit": True, "limits": _limits(request)}


@router.post("/orders")
async def submit_spread(ticket: SpreadTicket, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        order = await (await _service(request, user)).submit(ticket)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim spread submission failed for %s", ticket.underlying)
        raise HTTPException(status_code=502, detail="Failed to book the spread")
    return {"order": order}


@router.get("/orders")
async def list_orders(request: Request, status: str = "open", user: dict = Depends(get_current_user)) -> dict:
    """The book's packages: resting ("open"), finished ("closed") or all."""
    try:
        orders = await (await _service(request, user)).orders(status)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim option orders fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read the simulated option orders")
    return {"orders": orders, "status": status}


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        await (await _service(request, user)).cancel(order_id)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim option order cancel failed for %s", order_id)
        raise HTTPException(status_code=502, detail="Failed to cancel the simulated option order")
    return {"cancelled": order_id}


@router.get("/spreads")
async def spreads(request: Request, user: dict = Depends(get_current_user)) -> dict:
    store = getattr(request.app.state, "options_trigger_store", None)
    try:
        groups = await (await _service(request, user)).spreads()
        triggers = await store.list_for_user(user["id"], SIM_ACCOUNT) if store is not None else []
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim open spreads fetch failed")
        raise HTTPException(status_code=502, detail="Failed to read the simulated spreads")
    return {"spreads": [g.to_dict() for g in groups], "triggers": triggers}


@router.post("/spreads/close/preview")
async def preview_close(body: CloseSpreadRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        return await (await _service(request, user)).preview_close(body)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim close preview failed")
        raise HTTPException(status_code=502, detail="Failed to price the close")


@router.post("/spreads/payoff")
async def spread_payoff(body: PayoffRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        return (await (await _service(request, user)).payoff_for_held(body)).model_dump(mode="json")
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim payoff failed")
        raise HTTPException(status_code=502, detail="Failed to build the risk chart")


@router.post("/spreads/close")
async def close_spread(body: CloseSpreadRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    try:
        order = await (await _service(request, user)).close_spread(body)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim spread close failed")
        raise HTTPException(status_code=502, detail="Failed to book the closing order")
    return {"order": order}


# --- triggers -----------------------------------------------------------------


@router.post("/idea")
async def suggest_idea(body: IdeaRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """The options suggestion over the simulated book -- same four steps
    as routers/trading_options.py's /idea (see app.ai.options_suggest),
    priced through this service so collateral and level are the sim
    book's. Simulation without a replay shows the live chain, so a
    structure proposed here is as well founded as one on the paper
    account; only the fill is simulated.

    Refused during a history replay: that chain is synthetic (bid/ask
    derived from the last print, IV solved back out of it, no open
    interest) and the rest of the context -- GEX, news, earnings, IV
    history -- is today's, which for a past date is look-ahead. A
    suggestion built on that would read far better founded than it is.
    The widget hides the tab while a session is active; this is the same
    answer for a call that arrives anyway.
    """
    anthropic_client = getattr(request.app.state, "anthropic_client", None)
    if anthropic_client is None:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")
    if await _replay_seam(request, user["id"]) is not None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "replay_active",
                "message": (
                    "Not offered during a history replay: the replayed chain is synthetic and the "
                    "market context would be today's, not the replayed day's."
                ),
                "field": None,
            },
        )

    service = await _service(request, user)
    engine = getattr(request.app.state, "scanner_engine", None)
    try:
        return await suggest_options_ideas(
            anthropic_client,
            service,
            request.app.state.alpaca_clients,
            body.underlying,
            gex_cache=getattr(engine, "gex_cache", None),
            iv_store=getattr(request.app.state, "iv_history_store", None),
            earnings_calendar=getattr(request.app.state, "earnings_calendar", None),
        )
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim options idea generation failed for %s", body.underlying)
        raise HTTPException(status_code=502, detail="Failed to generate an options idea")


@router.get("/triggers")
async def list_triggers(request: Request, user: dict = Depends(get_current_user)) -> dict:
    return {"triggers": await _trigger_store(request).list_for_user(user["id"], SIM_ACCOUNT)}


@router.post("/triggers")
async def create_trigger(body: TriggerCreate, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """Arm a stop and/or target on the simulated position; checked by the
    book's own loop (live, or per replay tick) against the book's prices."""
    try:
        service = await _service(request, user)
        spot = await service.spot(body.underlying.upper())
        if spot is not None:
            if body.close_below is not None and body.close_below >= spot:
                raise TradingError(
                    f"close_below {body.close_below:g} is not below the current price {spot:.2f}", field="close_below"
                )
            if body.close_above is not None and body.close_above <= spot:
                raise TradingError(
                    f"close_above {body.close_above:g} is not above the current price {spot:.2f}", field="close_above"
                )
        trigger = await _trigger_store(request).create(user["id"], SIM_ACCOUNT, body)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Sim trigger create failed")
        raise HTTPException(status_code=502, detail="Failed to arm the trigger")
    return {"trigger": trigger}


@router.delete("/triggers/{trigger_id}")
async def cancel_trigger(trigger_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    if not await _trigger_store(request).cancel(user["id"], trigger_id):
        raise HTTPException(status_code=404, detail="No such trigger")
    return {"cancelled": trigger_id}
