import asyncio
from datetime import datetime, timedelta, timezone

from alpaca.data.enums import Adjustment
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from fastapi import APIRouter, HTTPException, Query, Request

from app.market_data.bars import get_intraday_minute_bars
from app.market_data.vwap import SessionVwapState

router = APIRouter(prefix="/api/symbols", tags=["symbols"])

# Timeframes beyond 1-minute intraday are fetched at their native Alpaca
# resolution over a lookback long enough to fill the chart, rather than
# aggregated client-side from 1-minute bars (which would mean fetching and
# shipping tens of thousands of bars for a monthly view). Session-anchored
# VWAP doesn't carry meaning once a bar spans more than one session, so these
# timeframes return an empty vwap series.
_HISTORICAL_TIMEFRAMES: dict[str, tuple[TimeFrame, timedelta]] = {
    "1Hour": (TimeFrame(1, TimeFrameUnit.Hour), timedelta(days=60)),
    "4Hour": (TimeFrame(4, TimeFrameUnit.Hour), timedelta(days=200)),
    "1Day": (TimeFrame(1, TimeFrameUnit.Day), timedelta(days=730)),
    "1Week": (TimeFrame(1, TimeFrameUnit.Week), timedelta(days=365 * 5)),
    "1Month": (TimeFrame(1, TimeFrameUnit.Month), timedelta(days=365 * 10)),
}


def _bar_to_dict(bar) -> dict:
    return {
        "t": bar.timestamp.isoformat(),
        "o": bar.open,
        "h": bar.high,
        "l": bar.low,
        "c": bar.close,
        "v": bar.volume,
    }


@router.get("/search")
async def search_symbols(q: str, request: Request) -> dict:
    universe = request.app.state.universe
    q_upper = q.upper()
    matches = [s for s in universe if s.startswith(q_upper)][:20]
    return {"matches": matches}


@router.get("/{symbol}/bars")
async def get_symbol_bars(
    symbol: str, request: Request, timeframe: str = Query(default="1Min")
) -> dict:
    symbol = symbol.upper()
    clients = request.app.state.alpaca_clients
    if not clients.settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")

    if timeframe != "1Min":
        config = _HISTORICAL_TIMEFRAMES.get(timeframe)
        if config is None:
            raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")
        alpaca_timeframe, lookback = config

        bar_request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=alpaca_timeframe,
            start=datetime.now(timezone.utc) - lookback,
            feed=clients.feed,
            adjustment=Adjustment.RAW,
        )
        bar_set = await asyncio.to_thread(clients.data.get_stock_bars, bar_request)
        bars = bar_set.data.get(symbol, [])
        return {
            "symbol": symbol,
            "bars": [_bar_to_dict(b) for b in bars],
            "vwap": [None] * len(bars),
        }

    bars = await get_intraday_minute_bars(clients, symbol)
    if not bars:
        return {"symbol": symbol, "bars": [], "vwap": []}

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

    return {
        "symbol": symbol,
        "bars": [_bar_to_dict(b) for b in bars],
        "vwap": vwap_series,
    }
