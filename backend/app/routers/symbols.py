import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from app.alpaca.client import AlpacaClients
from app.indicators.context import build_context
from app.indicators.loader import run_indicators
from app.market_data.bars import (
    HISTORICAL_TIMEFRAMES,
    get_historical_bars,
    get_intraday_minute_bars,
    intraday_chart_lookback_start_utc,
)
from app.market_data.vwap import SessionVwapState
from app.symbols.info import get_symbol_info

router = APIRouter(prefix="/api/symbols", tags=["symbols"])

# How far back an `around` request (see get_symbol_bars) will widen the
# 1-minute fetch to reach a specific historical moment -- beyond this,
# minute-level detail is rarely useful and the payload gets large, so the
# chart falls back to its normal default window and a coarser timeframe
# instead (see lightweight_chart.html's ESCALATION_ORDER).
_AROUND_MAX_LOOKBACK_DAYS = 60

# Extra trading sessions fetched *before* the `around` moment's own
# session -- without this, the fetch would start right at the target bar
# with nothing earlier in the array, and the chart's own centering logic
# (lightweight_chart.html's setChartData) can only clamp to showing the
# target at the left edge, never centered, since there'd be no bars to its
# left to center against.
_AROUND_LEFT_PADDING_SESSIONS = 5


def _bar_to_dict(bar) -> dict:
    return {
        "t": bar.timestamp.isoformat(),
        "o": bar.open,
        "h": bar.high,
        "l": bar.low,
        "c": bar.close,
        "v": bar.volume,
    }


async def _already_fetched(bars: list) -> list:
    """Lets a bar list the caller already holds take part in the gather below
    without a second round trip."""
    return bars


async def _compute_indicators(
    clients: AlpacaClients,
    symbol: str,
    minute_bars: list,
    timeframe: str,
    hourly_bars: list | None = None,
) -> list[dict]:
    """Reference lines and overlays for the chart.

    Each is computed from its own source data rather than from the bars the
    caller asked for, so a level means the same thing at every zoom. What the
    timeframe *does* decide is which indicators run at all: one describing a
    period the chart shows as a single candle is noise, so each declares a
    ceiling and the loader drops the rest. See MAX_TIMEFRAME in
    app.indicators.loader.

    Weekly/monthly bars are two independent Alpaca calls -- run them
    concurrently rather than sequentially so this doesn't add up to two
    extra round-trips' worth of latency on top of the bars fetch on every
    single chart load (this endpoint fires on every symbol click,
    including rapid ones from the heatmap).
    """
    weekly_bars, monthly_bars, anchor_bars = await asyncio.gather(
        get_historical_bars(clients, symbol, "1Week"),
        get_historical_bars(clients, symbol, "1Month"),
        # Hourly bars are the structural anchor (see the market-structure
        # indicator), needed whatever the chart is showing -- except when the
        # chart *is* hourly and the caller already has them.
        _already_fetched(hourly_bars)
        if hourly_bars is not None
        else get_historical_bars(clients, symbol, "1Hour"),
    )
    ctx = build_context(symbol, minute_bars, weekly_bars, monthly_bars, timeframe, anchor_bars)
    # Off the event loop: run_indicators is over a second of pure CPU (the
    # strategy walk, the level scan, pandas resampling), and this endpoint
    # shares its loop with the scanner engine's poll tick -- run inline it
    # both stalls every concurrent request and queues behind none of its
    # own awaits.
    return await asyncio.to_thread(run_indicators, ctx)


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
    symbol: str,
    request: Request,
    timeframe: str = Query(default="1Min"),
    around: int | None = Query(
        default=None,
        description=(
            "Unix seconds -- for timeframe=1Min, widens the fetch's start "
            "far enough back to include this specific moment (capped at "
            "_AROUND_MAX_LOOKBACK_DAYS), instead of just the default "
            "recent-sessions window."
        ),
    ),
) -> dict:
    symbol = symbol.upper()
    clients = request.app.state.alpaca_clients
    if not clients.settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials not configured")

    if timeframe != "1Min":
        if timeframe not in HISTORICAL_TIMEFRAMES:
            raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")
        # bars here are hourly/daily/etc, not minute bars -- indicators need
        # today's minute bars regardless, so fetch those separately. The two
        # are independent Alpaca calls -- run concurrently, same reasoning
        # as _compute_indicators' own weekly/monthly gather below.
        bars, minute_bars = await asyncio.gather(
            get_historical_bars(clients, symbol, timeframe),
            get_intraday_minute_bars(clients, symbol),
        )
        indicators = await _compute_indicators(
            clients, symbol, minute_bars, timeframe, bars if timeframe == "1Hour" else None
        )
        return {
            "symbol": symbol,
            "bars": [_bar_to_dict(b) for b in bars],
            "vwap": [None] * len(bars),
            "vwap_premarket": [None] * len(bars),
            "indicators": indicators,
        }

    start = None
    if around is not None:
        around_dt = datetime.fromtimestamp(around, tz=timezone.utc)
        if (datetime.now(timezone.utc) - around_dt).days <= _AROUND_MAX_LOOKBACK_DAYS:
            # Premarket open of the Nth session before (and including)
            # around_dt's own session -- see _AROUND_LEFT_PADDING_SESSIONS
            # for why this needs left padding, not just the target's own
            # session start. `end` isn't passed, so this still fetches all
            # the way through to right now, same as the default window
            # just starting earlier, not a narrow slice.
            start = intraday_chart_lookback_start_utc(now=around_dt, sessions=_AROUND_LEFT_PADDING_SESSIONS)

    bars = await get_intraday_minute_bars(clients, symbol, start=start)
    if not bars:
        return {"symbol": symbol, "bars": [], "vwap": [], "vwap_premarket": [], "indicators": []}

    # Replay bars through a fresh accumulator to build the VWAP series for
    # the chart backfill...
    vwap_state = SessionVwapState(symbol=symbol)
    vwap_series = []
    vwap_premarket_series = []
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
        vwap_premarket_series.append(vwap_state.premarket_anchored_vwap)

    # ...then copy the resulting accumulator into StreamManager's live state
    # for this symbol, so the first live tick after backfill continues the
    # same running total instead of resetting VWAP to zero.
    stream_manager = request.app.state.stream_manager
    live_state = stream_manager.get_or_create_vwap_state(symbol)
    live_state.cum_pv = vwap_state.cum_pv
    live_state.cum_vol = vwap_state.cum_vol
    live_state.cum_pv_premarket = vwap_state.cum_pv_premarket
    live_state.cum_vol_premarket = vwap_state.cum_vol_premarket
    live_state.session_date = vwap_state.session_date

    indicators = await _compute_indicators(clients, symbol, bars, timeframe)

    return {
        "symbol": symbol,
        "bars": [_bar_to_dict(b) for b in bars],
        "vwap": vwap_series,
        "vwap_premarket": vwap_premarket_series,
        "indicators": indicators,
    }
