from fastapi import APIRouter, HTTPException, Query, Request

from app.alpaca.client import AlpacaClients
from app.indicators.context import build_context
from app.indicators.loader import run_indicators
from app.market_data.bars import HISTORICAL_TIMEFRAMES, get_historical_bars, get_intraday_minute_bars
from app.market_data.vwap import SessionVwapState
from app.symbols.info import get_symbol_info

router = APIRouter(prefix="/api/symbols", tags=["symbols"])


def _bar_to_dict(bar) -> dict:
    return {
        "t": bar.timestamp.isoformat(),
        "o": bar.open,
        "h": bar.high,
        "l": bar.low,
        "c": bar.close,
        "v": bar.volume,
    }


async def _compute_indicators(clients: AlpacaClients, symbol: str, minute_bars: list) -> list[dict]:
    """Indicators (premarket/weekly/monthly range) are reference lines
    independent of the chart's own zoom -- always computed the same way
    regardless of which timeframe the caller actually requested.
    """
    weekly_bars = await get_historical_bars(clients, symbol, "1Week")
    monthly_bars = await get_historical_bars(clients, symbol, "1Month")
    ctx = build_context(symbol, minute_bars, weekly_bars, monthly_bars)
    return run_indicators(ctx)


@router.get("/search")
async def search_symbols(q: str, request: Request) -> dict:
    universe = request.app.state.universe
    q_upper = q.upper()
    matches = [s for s in universe if s.startswith(q_upper)][:20]
    return {"matches": matches}


@router.get("/{symbol}/info")
async def get_symbol_info_endpoint(symbol: str, request: Request) -> dict:
    symbol = symbol.upper()
    clients = request.app.state.alpaca_clients
    if not clients.settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")

    info = await get_symbol_info(request.app.state.fundamentals, clients, symbol)
    return info.model_dump(mode="json")


@router.get("/{symbol}/bars")
async def get_symbol_bars(
    symbol: str, request: Request, timeframe: str = Query(default="1Min")
) -> dict:
    symbol = symbol.upper()
    clients = request.app.state.alpaca_clients
    if not clients.settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")

    if timeframe != "1Min":
        if timeframe not in HISTORICAL_TIMEFRAMES:
            raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")
        bars = await get_historical_bars(clients, symbol, timeframe)
        # bars here are hourly/daily/etc, not minute bars -- indicators need
        # today's minute bars regardless, so fetch those separately.
        minute_bars = await get_intraday_minute_bars(clients, symbol)
        indicators = await _compute_indicators(clients, symbol, minute_bars)
        return {
            "symbol": symbol,
            "bars": [_bar_to_dict(b) for b in bars],
            "vwap": [None] * len(bars),
            "indicators": indicators,
        }

    bars = await get_intraday_minute_bars(clients, symbol)
    if not bars:
        return {"symbol": symbol, "bars": [], "vwap": [], "indicators": []}

    # Replay bars through a fresh accumulator to build the VWAP series for
    # the chart backfill...
    vwap_state = SessionVwapState(symbol=symbol)
    vwap_series = []
    for bar in bars:
        vwap = vwap_state.update(
            timestamp=bar.timestamp,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            bar_vwap=getattr(bar, "vwap", None),
        )
        vwap_series.append(vwap)

    # ...then copy the resulting accumulator into StreamManager's live state
    # for this symbol, so the first live tick after backfill continues the
    # same running total instead of resetting VWAP to zero.
    stream_manager = request.app.state.stream_manager
    live_state = stream_manager.get_or_create_vwap_state(symbol)
    live_state.cum_pv = vwap_state.cum_pv
    live_state.cum_vol = vwap_state.cum_vol
    live_state.session_date = vwap_state.session_date

    indicators = await _compute_indicators(clients, symbol, bars)

    return {
        "symbol": symbol,
        "bars": [_bar_to_dict(b) for b in bars],
        "vwap": vwap_series,
        "indicators": indicators,
    }
