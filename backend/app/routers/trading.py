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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth.dependency import get_current_user
from app.trading.errors import BrokerNotConnected, TradingError
from app.trading.guards import LIVE_CONFIRM_HEADER, Account, can_submit, limits_for
from app.trading.models import OrderTicket
from app.trading.service import OrderService

logger = logging.getLogger(__name__)

# No prefix of its own: main.py mounts this router twice, under /api/trading
# for the paper account and under /api/trading/live for the real one, with
# mark_live_account as the only difference. Every handler asks _account()
# which one it is serving.
router = APIRouter(tags=["trading"])


def mark_live_account(request: Request) -> None:
    """Dependency on the /api/trading/live mount."""
    request.state.trading_account = "live"


def _account(request: Request) -> Account:
    return getattr(request.state, "trading_account", "paper")


def _confirm(request: Request) -> str | None:
    """The typed LIVE from a live-mode dialog. Ignored for paper."""
    return request.headers.get(LIVE_CONFIRM_HEADER)


async def _service(request: Request, user: dict = Depends(get_current_user)) -> OrderService:
    """The order service on *this user's* broker account (see
    app.broker.resolver): their own keys, or the operator's from .env for
    the admin. No keys for the account -> 503 with a typed detail the UI
    turns into "connect your broker"."""
    settings = request.app.state.settings
    if not settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")
    account = _account(request)
    resolver = getattr(request.app.state, "broker_resolver", None)
    broker = None
    live_available: bool | None = None
    broker_info: dict = {"source": "env", "key_hint": None}
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
        broker_info = {"source": creds.source, "key_hint": creds.key_hint}
    elif account == "live" and not settings.has_live_credentials:
        raise HTTPException(status_code=503, detail="Live account not configured")
    service = OrderService(
        request.app.state.alpaca_clients,
        settings,
        engine=getattr(request.app.state, "scanner_engine", None),
        account=account,
        broker=broker,
        live_available=live_available,
    )
    # What the handlers need to know about the caller, without a second
    # dependency: whose trips to record, and what the account line shows.
    service.user_id = int(user["id"])  # type: ignore[attr-defined]
    service.include_legacy_trades = bool(user.get("is_admin"))  # type: ignore[attr-defined]
    service.broker_info = broker_info  # type: ignore[attr-defined]
    return service


@router.get("/account")
async def get_account(request: Request, service: OrderService = Depends(_service)) -> dict:
    """Buying power, equity and cash for the connected account.

    Also reports which mode the app is in, so the UI can label itself rather
    than the user having to remember what is in their .env -- the difference
    between simulated and real is not something to leave implicit.
    """
    settings = request.app.state.settings
    try:
        account = await service.account()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Alpaca account fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    which = _account(request)
    live_available = getattr(service, "_live_available", None)
    return {
        "account": account,
        "trading_account": which,
        "paper": settings.alpaca_paper and which == "paper",
        "live_available": settings.has_live_credentials if live_available is None else live_available,
        "broker": {**getattr(service, "broker_info", {}), "account_number": account.get("account_number")},
        "live_allowed": settings.trading_allow_live,
        "limits": limits_for(settings, which).to_dict(),
        "trading_enabled": settings.trading_enabled,
        # Prefills the ticket, so changing the setting is reflected in the UI
        # rather than the two drifting apart.
        "default_risk_pct": settings.trading_default_risk_pct,
    }


@router.get("/positions")
async def get_positions(request: Request, service: OrderService = Depends(_service)) -> dict:
    try:
        positions = await service.positions()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Alpaca positions fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    return {"positions": positions}


@router.get("/orders")
async def get_orders(request: Request, service: OrderService = Depends(_service), status: str = "open") -> dict:
    """Working orders by default; pass status=all or closed for history."""
    try:
        orders = await service.orders(status)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Alpaca orders fetch failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")
    return {"orders": orders, "status": status}


@router.get("/trades")
async def get_trades(
    request: Request, service: OrderService = Depends(_service),
    # ?range=day|week|month|all -- calendar periods in ET. Aliased for the
    # same reason as portfolio-history's. An unknown value is refused by
    # the service and arrives as the usual typed 422.
    range_key: str = Query("all", alias="range"),
) -> dict:
    """Closed round trips with realized P&L, newest first, plus a summary
    and per-day subtotals for the requested period.

    Read-only like the other history routes, and each call also records
    any newly closed trip -- see OrderService.sync_trades for why the
    store, not the broker, is the record.
    """
    store = getattr(request.app.state, "trade_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Trade record not initialised")
    try:
        return await service.sync_trades(
            store, range_key, user_id=getattr(service, "user_id", 0),
            include_legacy=getattr(service, "include_legacy_trades", True),
        )
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Trade sync failed")
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")


@router.get("/journal")
async def get_journal(request: Request, user: dict = Depends(get_current_user)) -> dict:
    """This user's notes on closed trades -- see app.trading.journal_store.
    Keyed by trade_id; a trade with no entry simply isn't in the dict.
    trade_id works for both a real Trade.id and a Simulation Mode trade's id
    (see JournalStore's own docstring), so this endpoint doesn't distinguish
    between them -- the frontend fetches whichever trade list the current
    trading mode implies (see api/tradingMode.ts) and matches by id.
    """
    store = request.app.state.journal_store
    entries = await store.list_entries(user["id"])
    return {"entries": {trade_id: entry.to_dict() for trade_id, entry in entries.items()}}


class JournalEntryRequest(BaseModel):
    note: str = Field(max_length=2000)
    rating: int | None = Field(default=None, ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=10)


@router.post("/journal/{trade_id}")
async def save_journal_entry(
    trade_id: str, body: JournalEntryRequest, request: Request, user: dict = Depends(get_current_user)
) -> dict:
    tags = [t.strip() for t in body.tags if t.strip()][:10]
    for tag in tags:
        if len(tag) > 30:
            raise HTTPException(status_code=422, detail=f"Tag {tag!r} is too long (max 30 characters)")
    store = request.app.state.journal_store
    entry = await store.upsert_entry(user["id"], trade_id, body.note, body.rating, tags)
    return {"entry": entry.to_dict()}


@router.get("/portfolio-history")
async def get_portfolio_history(
    request: Request, service: OrderService = Depends(_service),
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
        return await service.portfolio_history(range_key)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Alpaca portfolio history fetch failed for range %s", range_key)
        raise HTTPException(status_code=502, detail="Failed to reach the trading API")


@router.get("/reference-price/{symbol}")
async def get_reference_price(symbol: str, request: Request, service: OrderService = Depends(_service)) -> dict:
    """Last price for `symbol`, independent of any ticket -- what the
    Stop/Target auto-suggestion in the ticket sizes off before a ticket
    exists to preview. previewOrder can't fill this role: it's gated on the
    ticket already being complete (a stop price, for risk sizing), which is
    exactly the field this is suggesting a starting value for.
    """
    try:
        price = await service.reference_price(symbol.upper())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Reference-price lookup failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to fetch reference price")
    return {"price": price}


@router.get("/day-high/{symbol}")
async def get_day_high(symbol: str, request: Request, service: OrderService = Depends(_service)) -> dict:
    """Today's high for `symbol` -- the trigger price a breakout-entry
    hotkey sizes off. Ungated like the other read paths: this is a quote,
    not an order.
    """
    try:
        high = await service.day_high(symbol.upper())
    except HTTPException:
        raise
    except Exception:
        logger.exception("Day-high lookup failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to fetch day high")
    return {"day_high": high}


@router.post("/orders/preview")
async def preview_order(ticket: OrderTicket, request: Request, service: OrderService = Depends(_service)) -> dict:
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
        resolved = await service.preview(ticket)
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Order preview failed for %s", ticket.symbol)
        raise HTTPException(status_code=502, detail="Failed to price the order")

    limits = limits_for(settings, _account(request))
    return {
        "order": resolved.model_dump(mode="json"),
        "can_submit": can_submit(settings, _account(request), live_available=getattr(service, "_live_available", None)),
        "limits": {
            "account": limits.account,
            "max_order_qty": limits.max_order_qty,
            "max_order_notional": limits.max_order_notional,
            "default_risk_pct": settings.trading_default_risk_pct,
        },
    }


@router.post("/orders")
async def submit_order(ticket: OrderTicket, request: Request, service: OrderService = Depends(_service)) -> dict:
    """Place an order.

    Every refusal -- switched off, live account, bad stop, past a ceiling,
    or the broker's own -- arrives as a 422 with the same structured body
    the preview uses, so the ticket renders them all through one path.
    """
    try:
        order = await service.submit(ticket, confirm=_confirm(request))
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
async def replace_stop(order_id: str, body: ReplaceStopRequest, request: Request, service: OrderService = Depends(_service)) -> dict:
    """Move a working stop -- the edited SL cell and the break-even button."""
    try:
        order = await service.replace_stop(order_id, body.symbol, body.stop_price, confirm=_confirm(request))
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Stop replace failed for %s", order_id)
        raise HTTPException(status_code=502, detail="Failed to move the stop")
    return {"order": order}


class ReplaceTargetRequest(BaseModel):
    """Body of the target-move endpoint -- same cross-check convention as
    ReplaceStopRequest, for the take-profit leg."""

    symbol: str = Field(min_length=1)
    limit_price: float


@router.patch("/orders/{order_id}/target")
async def replace_target(order_id: str, body: ReplaceTargetRequest, request: Request, service: OrderService = Depends(_service)) -> dict:
    """Move a working take-profit -- a distinct path from the stop-move
    route above since the two need different body shapes for the same
    underlying resource."""
    try:
        order = await service.replace_target(order_id, body.symbol, body.limit_price, confirm=_confirm(request))
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Target replace failed for %s", order_id)
        raise HTTPException(status_code=502, detail="Failed to move the target")
    return {"order": order}


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, request: Request, service: OrderService = Depends(_service)) -> dict:
    try:
        await service.cancel(order_id, confirm=_confirm(request))
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
    request: Request, service: OrderService = Depends(_service),
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
        order = await service.close_position(symbol, qty, confirm=_confirm(request))
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Close position failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to close the position")
    return {"order": order}
