from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request

from app.market_data.gamma_exposure import call_wall, gamma_flip_strike, put_wall, top_walls
from app.market_data.gex_plan import compute_gex_plan
from app.services.market_clock import current_session

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "has_alpaca_credentials": settings.has_credentials,
        "has_anthropic_credentials": settings.has_anthropic_credentials,
        "data_feed": settings.alpaca_data_feed,
        "universe_size": len(request.app.state.universe),
    }


@router.get("/session")
async def session(_: Request) -> dict:
    return {
        "session": current_session(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/market-conditions")
async def market_conditions(request: Request) -> dict:
    """Red/yellow/green market-conditions readout -- see
    app.market_data.market_conditions. `available: false` (not an error)
    when FMP_API_KEY isn't configured or the first refresh hasn't
    completed yet, same "missing just means not available right now"
    framing as every other FMP-dependent feature in this app.
    """
    engine = request.app.state.scanner_engine
    conditions = engine.market_conditions if engine is not None else None
    if conditions is None:
        return {"available": False}
    return {
        "available": True,
        "level": conditions.level,
        "reasons": conditions.reasons,
        "vix": (
            {"price": conditions.vix.price, "change_pct": conditions.vix.change_pct}
            if conditions.vix
            else None
        ),
        "high_impact_events_today": [
            {
                "date": e.date.isoformat(),
                "country": e.country,
                "event": e.event,
                "impact": e.impact,
            }
            for e in conditions.high_impact_events_today
        ],
        "breadth_pct": conditions.breadth_pct,
    }


def _wall(row) -> dict | None:
    return {"strike": row.strike, "net_gex": row.net_gex} if row is not None else None


async def _readings(request: Request, symbol: str | None) -> dict:
    """The readings a /gex or /gex-plan call is about.

    Without `symbol`: everything currently held, which is what the two
    endpoints have always returned. With one: that symbol alone, computed
    on demand if the cache has nothing fresh -- so GEX is no longer limited
    to app.market_data.gamma_exposure.SYMBOLS. A cold symbol costs a real
    fetch (seconds), which is why the caller has to ask for it by name
    rather than getting it as part of a bulk response.
    """
    engine = request.app.state.scanner_engine
    if engine is None:
        return {}
    if symbol is None:
        return engine.gex
    reading = await engine.gex_cache.reading(symbol)
    return {} if reading is None else {reading.symbol: reading}


@router.get("/gex")
async def gex(request: Request, symbol: str | None = Query(default=None)) -> dict:
    """Net dealer gamma exposure plus each symbol's top-5 gamma-wall strikes
    by |net_gex|, explicit call/put wall strikes, and the approximate
    gamma-flip strike. Feeds the main chart's "GEX" level indicator.

    `symbol` asks for one ticker and computes it if it is not already
    cached -- any optionable symbol, not just the warmed ones. Omitting it
    returns every reading held, unchanged from before. `available: false`
    when there is nothing to report (no engine yet, or the symbol has no
    usable options chain), same framing as /market-conditions.

    Each reading carries `contracts_used` and `open_interest_used`: on a
    thin chain a "gamma wall" can rest on a handful of contracts, and the
    number is reported with what it rests on rather than suppressed by a
    liquidity threshold this app cannot defend.
    """
    readings = await _readings(request, symbol)

    return {
        "available": bool(readings),
        "symbols": {
            symbol_: {
                "spot_price": reading.spot_price,
                "as_of": reading.as_of.isoformat(),
                "net_gex": reading.net_gex,
                "call_gex": reading.call_gex,
                "put_gex": reading.put_gex,
                "contracts_used": reading.contracts_used,
                "open_interest_used": reading.open_interest_used,
                "top_walls": [
                    {"strike": row.strike, "net_gex": row.net_gex}
                    for row in top_walls(reading.by_strike, n=5)
                ],
                "call_wall": _wall(call_wall(reading.by_strike)),
                "put_wall": _wall(put_wall(reading.by_strike)),
                "gamma_flip_strike": gamma_flip_strike(reading.by_strike),
            }
            for symbol_, reading in readings.items()
        },
    }


@router.get("/gex-plan")
async def gex_plan(request: Request, symbol: str | None = Query(default=None)) -> dict:
    """Rule-based GEX playbook per symbol -- see app.market_data.gex_plan.
    Same source data, same on-demand `symbol` behaviour and same
    `available: false` framing as /gex, just shaped into a regime +
    plain-language playbook instead of chart levels.
    """
    readings = await _readings(request, symbol)

    symbols: dict = {}
    for symbol_, reading in readings.items():
        plan = compute_gex_plan(reading)
        symbols[symbol_] = {
            "regime": plan.regime,
            "near_flip": plan.near_flip,
            "gamma_flip_strike": plan.gamma_flip_strike,
            "call_wall": _wall(plan.call_wall),
            "put_wall": _wall(plan.put_wall),
            "playbook": plan.playbook,
            "contracts_used": reading.contracts_used,
            "open_interest_used": reading.open_interest_used,
        }
    return {"available": bool(readings), "symbols": symbols}
