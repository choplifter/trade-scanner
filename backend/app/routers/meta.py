from datetime import datetime, timezone

from fastapi import APIRouter, Request

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


@router.get("/gex")
async def gex(request: Request) -> dict:
    """Net dealer gamma exposure for each covered symbol (see
    app.market_data.gamma_exposure.SYMBOLS) plus each symbol's top-5
    gamma-wall strikes by |net_gex|, explicit call/put wall strikes, and the
    approximate gamma-flip strike. Feeds the main chart's "GEX" level
    indicator (frontend only draws it when the charted symbol is one of the
    covered ones). `available: false` before the first refresh has
    completed, same framing as /market-conditions.
    """
    engine = request.app.state.scanner_engine
    readings = engine.gex if engine is not None else {}

    def _wall(row) -> dict | None:
        return {"strike": row.strike, "net_gex": row.net_gex} if row is not None else None

    return {
        "available": bool(readings),
        "symbols": {
            symbol: {
                "spot_price": reading.spot_price,
                "as_of": reading.as_of.isoformat(),
                "net_gex": reading.net_gex,
                "call_gex": reading.call_gex,
                "put_gex": reading.put_gex,
                "top_walls": [
                    {"strike": row.strike, "net_gex": row.net_gex}
                    for row in top_walls(reading.by_strike, n=5)
                ],
                "call_wall": _wall(call_wall(reading.by_strike)),
                "put_wall": _wall(put_wall(reading.by_strike)),
                "gamma_flip_strike": gamma_flip_strike(reading.by_strike),
            }
            for symbol, reading in readings.items()
        },
    }


@router.get("/gex-plan")
async def gex_plan(request: Request) -> dict:
    """Rule-based GEX playbook per symbol -- see app.market_data.gex_plan.
    Same source data and `available: false` framing as /gex, just shaped
    into a regime + plain-language playbook instead of chart levels.
    """
    engine = request.app.state.scanner_engine
    readings = engine.gex if engine is not None else {}

    def _wall(row) -> dict | None:
        return {"strike": row.strike, "net_gex": row.net_gex} if row is not None else None

    symbols: dict = {}
    for symbol, reading in readings.items():
        plan = compute_gex_plan(reading)
        symbols[symbol] = {
            "regime": plan.regime,
            "near_flip": plan.near_flip,
            "gamma_flip_strike": plan.gamma_flip_strike,
            "call_wall": _wall(plan.call_wall),
            "put_wall": _wall(plan.put_wall),
            "playbook": plan.playbook,
        }
    return {"available": bool(readings), "symbols": symbols}
