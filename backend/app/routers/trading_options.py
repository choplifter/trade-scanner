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
from pydantic import BaseModel, Field

from app.ai.options_suggest import suggest_options_ideas
from app.auth.dependency import get_current_user
from app.options.models import CloseSpreadRequest, PayoffRequest, SpreadTicket, TriggerCreate
from app.options.service import OptionsService
from app.routers.trading import _account, _confirm
from app.trading.errors import BrokerNotConnected, TradingError
from app.trading.guards import can_submit, limits_for

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trading-options"])


async def _service(request: Request, user: dict = Depends(get_current_user)) -> OptionsService:
    """The options service on this user's broker account -- same
    resolution as routers/trading.py's _service."""
    settings = request.app.state.settings
    if not settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")
    account = _account(request)
    resolver = getattr(request.app.state, "broker_resolver", None)
    broker = None
    live_available: bool | None = None
    if resolver is not None:
        creds = await resolver.credentials(user, account)
        if creds is None:
            raise HTTPException(
                status_code=503,
                detail=BrokerNotConnected(
                    f"No Alpaca {account} account connected for {user['username']} -- "
                    "enter your API keys in Settings → Broker.",
                    field="account",
                ).to_detail(),
            )
        broker = await resolver.client(user, account)
        live_available = (await resolver.availability(user))["live"]
    elif account == "live" and not settings.has_live_credentials:
        raise HTTPException(status_code=503, detail="Live account not configured")
    return OptionsService(
        request.app.state.alpaca_clients,
        settings,
        engine=getattr(request.app.state, "scanner_engine", None),
        chain_cache=getattr(request.app.state, "options_chain_cache", None),
        account=account,
        broker=broker,
        live_available=live_available,
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
async def options_account(request: Request, service: OptionsService = Depends(_service)) -> dict:
    settings = request.app.state.settings
    try:
        account = await service.account()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Options account fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    return {
        **account,
        "can_submit": can_submit(settings, _account(request), live_available=getattr(service, "_live_available", None)),
        "feed": settings.alpaca_options_feed,
        "limits": _limits(request),
    }


@router.get("/expiries/{underlying}")
async def expiries(underlying: str, request: Request, service: OptionsService = Depends(_service)) -> dict:
    try:
        return await service.expiries(underlying.upper())
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Option expiries failed for %s", underlying)
        raise HTTPException(status_code=502, detail="Failed to load option expiries")


@router.get("/contract/{symbol}")
async def contract_quote(symbol: str, request: Request, service: OptionsService = Depends(_service)) -> dict:
    """One contract's live snapshot -- bid/ask/mid, greeks, IV -- for the
    premium chart's theta projection and expected-move band."""
    from app.options.chain_fetch import fetch_leg_quotes
    from app.options.occ import try_parse_occ

    symbol = symbol.upper()
    if try_parse_occ(symbol) is None:
        raise HTTPException(status_code=422, detail=f"Not an option contract symbol: {symbol}")
    try:
        quotes = await fetch_leg_quotes(request.app.state.alpaca_clients, [symbol])
    except Exception:
        logger.exception("Contract snapshot failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to load the contract snapshot")
    quote = quotes.get(symbol)
    if quote is None:
        raise HTTPException(status_code=404, detail=f"No snapshot for {symbol}")
    return quote.to_dict()


@router.get("/chain/{underlying}")
async def chain(
    underlying: str,
    request: Request, service: OptionsService = Depends(_service),
    expiry: date = Query(..., description="Expiration date, YYYY-MM-DD"),
) -> dict:
    try:
        return (await service.chain(underlying.upper(), expiry)).to_dict()
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Option chain failed for %s %s", underlying, expiry)
        raise HTTPException(status_code=502, detail="Failed to load the option chain")


@router.post("/preview")
async def preview_spread(ticket: SpreadTicket, request: Request, service: OptionsService = Depends(_service)) -> dict:
    settings = request.app.state.settings
    try:
        resolved = await service.preview(ticket)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Spread preview failed for %s", ticket.underlying)
        raise HTTPException(status_code=502, detail="Failed to price the spread")
    return {
        "spread": resolved.model_dump(mode="json"),
        "can_submit": can_submit(settings, _account(request), live_available=getattr(service, "_live_available", None)),
        "limits": _limits(request),
    }


@router.post("/orders")
async def submit_spread(ticket: SpreadTicket, request: Request, service: OptionsService = Depends(_service)) -> dict:
    try:
        order = await service.submit(ticket, confirm=_confirm(request))
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Spread submission failed for %s", ticket.underlying)
        raise HTTPException(status_code=502, detail="Failed to submit the spread")
    return {"order": order}


@router.get("/spreads")
async def spreads(request: Request, service: OptionsService = Depends(_service), user: dict = Depends(get_current_user)) -> dict:
    store = getattr(request.app.state, "options_trigger_store", None)
    try:
        groups = await service.spreads()
        triggers = await store.list_for_user(user["id"], _account(request)) if store is not None else []
    except HTTPException:
        raise
    except Exception:
        logger.exception("Open spreads fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    return {"spreads": [g.to_dict() for g in groups], "triggers": triggers}


@router.post("/spreads/close/preview")
async def preview_close(body: CloseSpreadRequest, request: Request, service: OptionsService = Depends(_service)) -> dict:
    try:
        return await service.preview_close(body)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Close preview failed")
        raise HTTPException(status_code=502, detail="Failed to price the close")


@router.post("/spreads/payoff")
async def spread_payoff(body: PayoffRequest, request: Request, service: OptionsService = Depends(_service)) -> dict:
    """The risk chart of a held position (see app.options.payoff)."""
    try:
        return (await service.payoff_for_held(body)).model_dump(mode="json")
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Payoff failed")
        raise HTTPException(status_code=502, detail="Failed to build the risk chart")


@router.post("/spreads/close")
async def close_spread(body: CloseSpreadRequest, request: Request, service: OptionsService = Depends(_service)) -> dict:
    try:
        order = await service.close_spread(body, confirm=_confirm(request))
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
async def create_trigger(body: TriggerCreate, request: Request, service: OptionsService = Depends(_service), user: dict = Depends(get_current_user)) -> dict:
    """Arm a stop and/or target on the underlying's price and/or on the
    position's own premium (the mark of the closing package). Arming a live
    trigger is itself a real-money decision, so it asks for the typed
    confirmation; the loop that later fires it does not ask again."""
    from app.trading.guards import assert_can_trade

    settings = request.app.state.settings
    account = _account(request)
    try:
        assert_can_trade(settings, account, _confirm(request), live_available=getattr(service, "_live_available", None))
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


class IdeaRequest(BaseModel):
    underlying: str = Field(min_length=1, max_length=12)


@router.post("/idea")
async def suggest_idea(
    body: IdeaRequest, request: Request, service: OptionsService = Depends(_service)
) -> dict:
    """Options structures for one underlying, proposed by Claude and priced
    here -- see app.ai.options_suggest for the four steps and why the
    pricing is not the model's.

    Lives on this router rather than its own so it inherits the account
    resolution `_service` already does (this user's broker keys, paper or
    live, the shared chain cache) and the same TradingError -> 422
    convention; a separate router would have to duplicate all of it.

    Read-only: nothing is ordered. The response carries a ready-made ticket
    per idea for the frontend to load, and the user still submits it by
    hand.
    """
    anthropic_client = getattr(request.app.state, "anthropic_client", None)
    if anthropic_client is None:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

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
    except Exception:
        logger.exception("Options idea generation failed for %s", body.underlying)
        raise HTTPException(status_code=502, detail="Failed to generate an options idea")
