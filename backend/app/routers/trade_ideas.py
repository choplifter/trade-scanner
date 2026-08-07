import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.ai.trade_idea_tracker import TrackedIdea
from app.ai.trade_ideas import generate_trade_ideas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["trade-ideas"])


@router.post("/trade-ideas")
async def post_trade_ideas(request: Request) -> dict:
    settings = request.app.state.settings
    if not settings.has_anthropic_credentials:
        raise HTTPException(
            status_code=503,
            detail=(
                "ANTHROPIC_API_KEY not configured -- add it to backend/.env "
                "to enable AI trade ideas."
            ),
        )

    engine = request.app.state.scanner_engine
    rows = engine.snapshot_view("gainers")
    if not rows:
        return {"ideas": [], "disclaimer": "No active movers to summarize right now."}

    client = request.app.state.anthropic_client
    alpaca = request.app.state.alpaca_clients
    try:
        result = await generate_trade_ideas(client, alpaca, rows)
    except Exception:
        logger.exception("Trade idea generation failed")
        raise HTTPException(status_code=502, detail="Failed to generate trade ideas")

    last_prices = {r.symbol: r.last_price for r in rows}
    exchange_by_symbol = {r.symbol: r.exchange for r in rows}
    generated_at = datetime.now(timezone.utc)
    tracked = [
        TrackedIdea(
            symbol=idea.symbol,
            headline=idea.headline,
            entry_price=last_prices[idea.symbol],
            generated_at=generated_at,
        )
        for idea in result.ideas
        if idea.symbol in last_prices
    ]
    request.app.state.trade_idea_tracker.record(tracked)

    # The LLM only knows the numbers it was asked to rank on, not the
    # listing exchange -- attached here from the same scanner rows so the
    # frontend can build an unambiguous TradingView symbol for click/copy.
    ideas_payload = [
        {**idea.model_dump(mode="json"), "exchange": exchange_by_symbol.get(idea.symbol, "")}
        for idea in result.ideas
    ]
    return {"ideas": ideas_payload, "disclaimer": result.disclaimer}


@router.get("/trade-ideas/performance")
async def get_trade_ideas_performance(request: Request) -> dict:
    """How past AI-generated picks have actually moved since they were
    generated -- reads current prices off the scanner engine's already-
    polled live rows, so this costs no extra API calls.
    """
    tracker = request.app.state.trade_idea_tracker
    engine = request.app.state.scanner_engine
    now = datetime.now(timezone.utc)

    picks = []
    for entry in tracker.all():
        current_row = engine.rows.get(entry.symbol)
        current_price = current_row.last_price if current_row else None
        pct_change_since_pick = (
            round((current_price - entry.entry_price) / entry.entry_price * 100, 2)
            if current_price is not None and entry.entry_price
            else None
        )
        picks.append(
            {
                "symbol": entry.symbol,
                "headline": entry.headline,
                "generated_at": entry.generated_at.isoformat(),
                "minutes_since": round((now - entry.generated_at).total_seconds() / 60, 1),
                "entry_price": round(entry.entry_price, 2),
                "current_price": round(current_price, 2) if current_price is not None else None,
                "pct_change_since_pick": pct_change_since_pick,
            }
        )
    picks.sort(key=lambda p: p["generated_at"], reverse=True)
    return {"picks": picks}
