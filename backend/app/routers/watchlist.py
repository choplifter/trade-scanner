import asyncio
import re

from alpaca.data.requests import StockSnapshotRequest
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependency import get_current_user
from app.scanners.formulas import pct_change, resolve_last_price
from app.watchlist.defaults import DEFAULT_WATCHLIST_SYMBOLS

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

# A watchlist is user-curated, not universe-filtered, so it has no natural
# size ceiling the way the scanner's ranked views do -- this is a fat-finger
# guard on the request itself, not a real usage limit.
_MAX_SYMBOLS = 200

# Loose on purpose, matching the frontend's own TICKER_RE (dragSymbol.ts) --
# a watchlist symbol can be anything Alpaca trades, not just what the
# scanner's universe ranks. Allows a "." for share classes like BRK.B.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


@router.get("/symbols")
async def get_watchlist_symbols(request: Request, user: dict = Depends(get_current_user)) -> dict:
    store = request.app.state.watchlist_store
    await store.seed_if_empty(user["id"], DEFAULT_WATCHLIST_SYMBOLS)
    return {"symbols": await store.list_symbols(user["id"])}


class AddSymbolRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)


@router.post("/symbols")
async def add_watchlist_symbol(
    body: AddSymbolRequest, request: Request, user: dict = Depends(get_current_user)
) -> dict:
    symbol = body.symbol.strip().upper()
    if not _TICKER_RE.match(symbol):
        raise HTTPException(status_code=422, detail=f"{body.symbol!r} doesn't look like a ticker.")
    store = request.app.state.watchlist_store
    await store.add_symbol(user["id"], symbol)
    return {"symbols": await store.list_symbols(user["id"])}


@router.delete("/symbols/{symbol}")
async def remove_watchlist_symbol(symbol: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    store = request.app.state.watchlist_store
    await store.remove_symbol(user["id"], symbol.strip().upper())
    return {"symbols": await store.list_symbols(user["id"])}


@router.get("/quotes")
async def get_watchlist_quotes(symbols: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """Last price / % change for an arbitrary symbol list, independent of the
    scanner's momentum universe (which excludes ETFs and anything outside
    the price/volume band -- see app.alpaca.universe.build_universe) and of
    StreamManager's live-subscription cap (see app.market_data.stream_manager),
    since a watchlist can hold more symbols than that cap allows. One batched
    snapshot request per poll rather than a subscription per symbol.
    """
    tickers = sorted({s.strip().upper() for s in symbols.split(",") if s.strip()})
    if not tickers:
        return {}
    if len(tickers) > _MAX_SYMBOLS:
        raise HTTPException(status_code=400, detail=f"Too many symbols (max {_MAX_SYMBOLS})")

    clients = request.app.state.alpaca_clients
    if not clients.settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")

    snapshots = await asyncio.to_thread(
        clients.data.get_stock_snapshot,
        StockSnapshotRequest(symbol_or_symbols=tickers, feed=clients.feed),
    )

    quotes: dict[str, dict] = {}
    for symbol, snap in snapshots.items():
        if snap is None:
            continue
        daily_bar = snap.daily_bar
        prev_close = snap.previous_daily_bar.close if snap.previous_daily_bar else None

        last = resolve_last_price(
            snap.latest_trade.price if snap.latest_trade else None,
            daily_bar.close if daily_bar else None,
            prev_close,
            daily_bar.low if daily_bar else None,
            daily_bar.high if daily_bar else None,
        )
        if last is None:
            # Nothing usable for this symbol (e.g. a bad ticker Alpaca still
            # echoed back an empty snapshot for) -- omit rather than show a
            # blank row the frontend would have to special-case.
            continue

        updated_at = None
        if snap.latest_trade is not None:
            updated_at = snap.latest_trade.timestamp.isoformat()
        elif daily_bar is not None:
            updated_at = daily_bar.timestamp.isoformat()

        quotes[symbol] = {
            "last": last,
            "prevClose": prev_close,
            "pctChange": pct_change(last, prev_close) if prev_close else None,
            "volume": daily_bar.volume if daily_bar else None,
            "updatedAt": updated_at,
        }

    return quotes
