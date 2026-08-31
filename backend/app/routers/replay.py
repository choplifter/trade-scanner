"""History-replay session control -- a steppable clock layered on top of
Simulation Mode (see app.replay's module docstrings for the design).

Per-user, same reasoning as routers/trading_sim.py: every endpoint takes
`user: dict = Depends(get_current_user)` and scopes to that user's own
session. Unlike trading_sim.py this genuinely needs Alpaca credentials for
every endpoint that touches an engine (start/state) -- there is no
"degraded but working" mode without historical bars to replay.
"""

import asyncio
import logging
from bisect import bisect_right
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependency import get_current_user
from app.indicators.context import build_context
from app.indicators.loader import run_indicators
from app.market_data.bars import get_historical_bars
from app.market_data.vwap import SessionVwapState
from app.replay.engine import load_replay_engine
from app.replay.loop import BAR_STEP
from app.routers.symbols import _bar_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/replay", tags=["replay"])

# How much extra calendar history to fetch beyond the requested range, so a
# session started mid-week still has a real previous close / 20-day volume
# baseline for its very first bar. Mirrors _WARMUP_CALENDAR_PADDING_DAYS's
# purpose but sized for the fetch call itself, not the trailing-average math.
_FETCH_PADDING_DAYS = 3

# Ceiling on a symbol list derived from ScannerHistoryStore.symbols_for_date
# -- a genuinely busy day can have appeared across gainers/losers/most_active
# for well over 50 distinct symbols, and a 5-minute-bar fetch cost scales
# roughly linearly with symbol count (see bar_cache.py's module docstring).
# Truncated to the symbols with the largest entry_pct_change magnitude on
# that day (the ones an actual trader would have most wanted to see), not an
# arbitrary DB-order slice.
_MAX_DERIVED_SYMBOLS = 40


def _lookback_days(start_date: date) -> int:
    today = datetime.now(UTC).date()
    return max((today - start_date).days + _FETCH_PADDING_DAYS, _FETCH_PADDING_DAYS)


class StartRequest(BaseModel):
    # Optional: omit (or send an empty list) to replay the real historical
    # "stocks in play" for start_date instead of a hand-picked list -- see
    # _derive_symbols below. Manually naming symbols still works, for
    # replaying a specific watchlist rather than whatever the scanner
    # actually flagged that day.
    symbols: list[str] = Field(default_factory=list, max_length=50)
    start_date: date
    speed: float = Field(default=1.0, gt=0, le=50)


class SeekRequest(BaseModel):
    as_of: datetime


class StepRequest(BaseModel):
    direction: Literal["forward", "backward"] = "forward"


class SpeedRequest(BaseModel):
    speed: float = Field(gt=0, le=50)


def _session_or_404(session: dict | None) -> dict:
    if session is None:
        raise HTTPException(status_code=404, detail="No replay session -- call /api/replay/start first")
    return session


async def _state_payload(request: Request, session: dict) -> dict:
    engine = request.app.state.replay_engines.get(session["user_id"])
    settings = request.app.state.settings
    views = None
    if engine is not None:
        as_of = datetime.fromisoformat(session["as_of"])
        views = {
            name: [r.model_dump(mode="json") for r in rows]
            for name, rows in engine.snapshot_views(as_of, settings.scanner_min_dollar_volume).items()
        }
    return {
        "session": session,
        "range": {
            "start": engine.start.isoformat() if engine and engine.start else None,
            "end": engine.end.isoformat() if engine and engine.end else None,
        },
        "views": views,
    }


@router.post("/start")
async def start_replay(body: StartRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    settings = request.app.state.settings
    if not settings.has_credentials:
        raise HTTPException(status_code=503, detail="Alpaca credentials are required to fetch historical bars")

    symbols = [s.strip().upper() for s in body.symbols if s.strip()]
    if not symbols:
        # No explicit list -- replay the real "stocks in play" for this date,
        # as the live scanner actually recorded them (see
        # ScannerHistoryStore.symbols_for_date), not a hand-picked list.
        symbols = await request.app.state.scanner_history_store.symbols_for_date(
            body.start_date.isoformat(), limit=_MAX_DERIVED_SYMBOLS
        )
        if not symbols:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"No recorded stocks-in-play for {body.start_date.isoformat()} -- "
                    "the scanner may not have been running that day, or it's a "
                    "non-trading day. Try another date, or enter symbols manually."
                ),
            )

    lookback_days = _lookback_days(body.start_date)
    try:
        engine = await load_replay_engine(request.app.state.alpaca_clients, symbols, lookback_days)
    except Exception:
        logger.exception("Replay bar fetch failed for user %s, symbols=%s", user["id"], symbols)
        raise HTTPException(status_code=502, detail="Failed to fetch historical bars") from None

    if not engine.timestamps:
        raise HTTPException(
            status_code=422,
            detail="No historical bars available for that symbol set / date range",
        )

    requested_start = datetime.combine(body.start_date, datetime.min.time(), tzinfo=UTC)
    as_of = engine.first_at_or_after(requested_start) or engine.start

    request.app.state.replay_engines.put(user["id"], engine)
    session = await request.app.state.replay_store.start(
        user["id"],
        symbols,
        body.start_date.isoformat(),
        (engine.end.date().isoformat() if engine.end else body.start_date.isoformat()),
        lookback_days,
        as_of.isoformat(),
        body.speed,
    )
    return await _state_payload(request, session)


@router.get("/state")
async def get_state(request: Request, user: dict = Depends(get_current_user)) -> dict:
    session = _session_or_404(await request.app.state.replay_store.get(user["id"]))
    return await _state_payload(request, session)


@router.post("/play")
async def play_replay(request: Request, user: dict = Depends(get_current_user)) -> dict:
    _session_or_404(await request.app.state.replay_store.get(user["id"]))
    session = await request.app.state.replay_store.update(user["id"], playing=1)
    return await _state_payload(request, session)


@router.post("/pause")
async def pause_replay(request: Request, user: dict = Depends(get_current_user)) -> dict:
    _session_or_404(await request.app.state.replay_store.get(user["id"]))
    session = await request.app.state.replay_store.update(user["id"], playing=0)
    return await _state_payload(request, session)


@router.post("/seek")
async def seek_replay(body: SeekRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    _session_or_404(await request.app.state.replay_store.get(user["id"]))
    as_of = body.as_of if body.as_of.tzinfo else body.as_of.replace(tzinfo=UTC)
    session = await request.app.state.replay_store.update(user["id"], as_of=as_of.isoformat())
    return await _state_payload(request, session)


def _stepped_as_of(
    as_of: datetime, direction: Literal["forward", "backward"], start: datetime | None, end: datetime | None
) -> datetime:
    """One BAR_STEP forward or backward from `as_of`, clamped to
    [start, end] -- the fetched range's own bounds, past which there is
    nothing to show. `start`/`end` of None (engine not resident, e.g. right
    after a server restart -- see get_replay_bars) skips clamping on that
    side; a subsequent request rebuilds the engine and read paths reconcile
    normally, same graceful fallback used elsewhere in this router.
    """
    stepped = as_of + (BAR_STEP if direction == "forward" else -BAR_STEP)
    if start is not None and stepped < start:
        return start
    if end is not None and stepped > end:
        return end
    return stepped


@router.post("/step")
async def step_replay(body: StepRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """Move the replay clock by exactly one bar -- the manual counterpart to
    one pacing-loop tick (see BAR_STEP's docstring in app.replay.loop), for
    studying a session bar-by-bar instead of watching it auto-play.

    Always pauses (playing=0) in the same update as the new as_of: a step is
    a deliberate manual placement, and should not be immediately overwritten
    by the pacing loop on its next tick if the session happened to be
    playing when the user stepped.
    """
    session = _session_or_404(await request.app.state.replay_store.get(user["id"]))
    engine = request.app.state.replay_engines.get(user["id"])
    as_of = datetime.fromisoformat(session["as_of"])
    new_as_of = _stepped_as_of(
        as_of, body.direction, engine.start if engine else None, engine.end if engine else None
    )
    session = await request.app.state.replay_store.update(user["id"], as_of=new_as_of.isoformat(), playing=0)
    return await _state_payload(request, session)


@router.patch("/speed")
async def set_speed(body: SpeedRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    _session_or_404(await request.app.state.replay_store.get(user["id"]))
    session = await request.app.state.replay_store.update(user["id"], speed=body.speed)
    return await _state_payload(request, session)


@router.get("/bars/{symbol}")
async def get_replay_bars(symbol: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """5-minute bars for `symbol` from the start of this session's fetched
    range through the current as_of -- what the replay chart draws (see
    ReplayEngine.bars_up_to). Clipped server-side rather than sending the
    whole fetched range and trusting the client to slice it, so a replay
    chart can never show a bar the clock hasn't reached yet, even
    transiently -- the entire point of "replay" is not seeing the future.

    Empty rather than a 404/502 when the session exists but has no
    resident engine (e.g. right after a server restart, before the pacing
    loop or another request has reloaded it) or the symbol wasn't part of
    this session's universe -- same "nothing to show yet, not an error"
    convention _state_payload's own `views: null` uses.
    """
    session = _session_or_404(await request.app.state.replay_store.get(user["id"]))
    engine = request.app.state.replay_engines.get(user["id"])
    if engine is None:
        return {"symbol": symbol.upper(), "bars": []}
    as_of = datetime.fromisoformat(session["as_of"])
    bars = engine.bars_up_to(symbol.upper(), as_of)
    return {"symbol": symbol.upper(), "bars": [_bar_to_dict(b) for b in bars]}


def _clip_to_as_of(bars: list, as_of: datetime) -> list:
    """`bars` (chronological, like every Alpaca bar list here) truncated to
    what was already knowable at `as_of` -- the same bisect_right approach
    ReplayEngine.bars_up_to uses for its own 5-minute bars, applied here to
    the weekly/monthly/hourly bars fetched fresh below. Without this, a
    weekly/monthly/hourly bar dated after the replayed moment (they're
    fetched live, unbounded by the replay clock) would leak into an
    indicator as something already known."""
    times = [b.timestamp for b in bars]
    return bars[: bisect_right(times, as_of)]


@router.get("/indicators/{symbol}")
async def get_replay_indicators(symbol: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """VWAP + the same reference-line/overlay indicators the live chart
    draws (see app.indicators.loader.run_indicators), computed against this
    replay session's bars instead of a live poll.

    Kept separate from /bars/{symbol}: that endpoint is a cheap in-memory
    slice refetched every clock tick during playback, while this one needs
    real Alpaca calls for weekly/monthly/hourly bars that don't need
    refreshing nearly that often.

    Every indicator file runs completely unmodified -- see build_context's
    `as_of` and app.indicators.context.prior_completed_period for why the
    weekly/monthly bars fetched here are clipped to the replayed moment
    before reaching them, rather than trusting real wall-clock "now".
    """
    session = _session_or_404(await request.app.state.replay_store.get(user["id"]))
    engine = request.app.state.replay_engines.get(user["id"])
    if engine is None:
        return {"symbol": symbol.upper(), "vwap": [], "vwap_premarket": [], "indicators": []}

    symbol = symbol.upper()
    as_of = datetime.fromisoformat(session["as_of"])
    minute_bars = engine.bars_up_to(symbol, as_of)

    clients = request.app.state.alpaca_clients
    weekly_bars, monthly_bars, hourly_bars = await asyncio.gather(
        get_historical_bars(clients, symbol, "1Week"),
        get_historical_bars(clients, symbol, "1Month"),
        get_historical_bars(clients, symbol, "1Hour"),
    )
    weekly_bars = _clip_to_as_of(weekly_bars, as_of)
    monthly_bars = _clip_to_as_of(monthly_bars, as_of)
    hourly_bars = _clip_to_as_of(hourly_bars, as_of)

    ctx = build_context(symbol, minute_bars, weekly_bars, monthly_bars, "1Min", hourly_bars, as_of=as_of)
    indicators = await asyncio.to_thread(run_indicators, ctx)

    # Same accumulator/loop symbols.py's live /bars endpoint runs -- no
    # stream-manager sync afterwards, since that continuation only matters
    # for a live websocket picking up where a backfill left off.
    vwap_state = SessionVwapState(symbol=symbol)
    vwap_series = []
    vwap_premarket_series = []
    for bar in minute_bars:
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

    return {
        "symbol": symbol,
        "vwap": vwap_series,
        "vwap_premarket": vwap_premarket_series,
        "indicators": indicators,
    }


@router.delete("/stop")
async def stop_replay(request: Request, user: dict = Depends(get_current_user)) -> dict:
    await request.app.state.replay_store.stop(user["id"])
    request.app.state.replay_engines.discard(user["id"])
    return {"stopped": True}
