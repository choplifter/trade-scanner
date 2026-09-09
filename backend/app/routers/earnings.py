"""The Earnings screen: who reports today and next session, the facts
that make one reporter's options different from another's, and -- per
symbol, on request -- the structures a rule matrix picks and the
Optimizer prices. See app.market_data.earnings_screen and
app.options.earnings_evaluate.

/screen and /facts read the chain through the shared cache with no
broker account behind them (spot, expiries, chain need none), so they
answer for every logged-in user regardless of broker keys. /evaluate
previews tickets and so goes through the same per-user service the
Optimizer uses. Error convention as the trading routers: TradingError ->
422 with {code, message, field}; anything else -> 502.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.market_data.earnings_screen import build_earnings_screen, gather_symbol_facts, liquidity_from_bars
from app.options.service import OptionsService
from app.routers.trading_options import _service as _account_service
from app.services.market_clock import ET, next_trading_day
from app.trading.errors import TradingError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/earnings", tags=["earnings"])

FACTS_MAX_SYMBOLS = 30
LIQUIDITY_LOOKBACK_DAYS = 35


def _market_service(request: Request) -> OptionsService:
    """Prices and chains only -- the shared chain cache, the operator's
    market-data client, no broker."""
    settings = request.app.state.settings
    if not settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")
    return OptionsService(
        request.app.state.alpaca_clients,
        settings,
        engine=getattr(request.app.state, "scanner_engine", None),
        chain_cache=getattr(request.app.state, "options_chain_cache", None),
    )


def _today(raw: str | None) -> date:
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD") from exc
    return datetime.now(ET).date()


def _tradable(request: Request) -> dict[str, str | None]:
    """Symbol -> company name for everything Alpaca lists; the scanner
    universe (no names) when the wider listing was not fetched."""
    listed = getattr(request.app.state, "all_symbols", None) or []
    if listed:
        return {s.symbol: s.name for s in listed}
    return {symbol: None for symbol in (getattr(request.app.state, "universe", None) or {})}


@router.get("/screen")
async def earnings_screen(
    request: Request,
    day: str | None = Query(default=None, alias="date"),
    min_dollar_volume: float | None = Query(default=None, ge=0),
) -> dict:
    """Today's and next session's reporters that Alpaca trades, above a
    trailing dollar-volume floor (the scanner's by default), most liquid
    first. `sources.calendar` is false when FMP is not configured or the
    call failed -- an empty list then means "not known", not "nobody"."""
    today = _today(day)
    next_session = next_trading_day(today)
    settings = request.app.state.settings
    floor = settings.scanner_min_dollar_volume if min_dollar_volume is None else min_dollar_volume

    calendar = getattr(request.app.state, "earnings_day_calendar", None)
    reporters = []
    calendar_ok = False
    if calendar is not None and calendar.configured:
        try:
            reporters = await calendar.reporters(today, next_session)
            calendar_ok = True
        except Exception:
            logger.exception("Earnings screen: calendar failed for %s..%s", today, next_session)

    tradable = _tradable(request)
    candidates = sorted({r.symbol for r in reporters if r.symbol in tradable})
    liquidity: dict = {}
    clients = getattr(request.app.state, "alpaca_clients", None)
    if candidates and clients is not None:
        try:
            from app.market_data.bars import get_daily_bars_multi

            bars = await get_daily_bars_multi(clients, candidates, lookback_days=LIQUIDITY_LOOKBACK_DAYS)
            liquidity = liquidity_from_bars(bars, today=today)
        except Exception:
            logger.exception("Earnings screen: daily bars failed for %d symbols", len(candidates))

    engine = getattr(request.app.state, "scanner_engine", None)
    rows = build_earnings_screen(
        reporters,
        tradable,
        liquidity,
        getattr(engine, "rows", None),
        today=today,
        next_session=next_session,
        min_dollar_volume=floor,
    )
    return {
        "today": today.isoformat(),
        "next_session": next_session.isoformat(),
        "min_dollar_volume": floor,
        "rows": [r.to_dict() for r in rows],
        "candidates": len(candidates),
        "sources": {"calendar": calendar_ok, "bars": bool(liquidity) or not candidates},
    }


@router.get("/facts")
async def earnings_facts(
    request: Request,
    symbols: str = Query(min_length=1),
    service: OptionsService = Depends(_market_service),
) -> dict:
    """Per symbol: the front expiry's implied move, its strike count, and
    the stock's moves over past reports. At most FACTS_MAX_SYMBOLS per
    call -- one expiries and one chain read each."""
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise HTTPException(status_code=422, detail="symbols must name at least one symbol")
    if len(wanted) > FACTS_MAX_SYMBOLS:
        raise HTTPException(status_code=422, detail=f"at most {FACTS_MAX_SYMBOLS} symbols per call")
    today = datetime.now(ET).date()
    facts = await gather_symbol_facts(
        wanted,
        service=service,
        clients=getattr(request.app.state, "alpaca_clients", None),
        earnings_calendar=getattr(request.app.state, "earnings_calendar", None),
        today=today,
    )
    return {"today": today.isoformat(), "facts": {symbol: f.to_dict() for symbol, f in facts.items()}}


@router.get("/evaluate/{symbol}")
async def earnings_evaluate(
    symbol: str,
    request: Request,
    service: OptionsService = Depends(_account_service),
) -> dict:
    """Signals, the rule matrix's scores with their reasons, and the
    Optimizer's priced structures for the picked families -- see
    app.options.earnings_evaluate. Read-only: every result carries a
    ticket the user still submits by hand."""
    from app.options.earnings_evaluate import evaluate_symbol

    engine = getattr(request.app.state, "scanner_engine", None)
    try:
        return await evaluate_symbol(
            service,
            getattr(request.app.state, "alpaca_clients", None),
            symbol.upper(),
            gex_cache=getattr(engine, "gex_cache", None),
            iv_store=getattr(request.app.state, "iv_history_store", None),
            earnings_calendar=getattr(request.app.state, "earnings_calendar", None),
            market_conditions=getattr(engine, "market_conditions", None),
        )
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Earnings evaluation failed for %s", symbol)
        raise HTTPException(status_code=502, detail="Failed to evaluate the symbol")
