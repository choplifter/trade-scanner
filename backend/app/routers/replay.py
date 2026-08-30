"""History-replay session control -- a steppable clock layered on top of
Simulation Mode (see app.replay's module docstrings for the design).

Per-user, same reasoning as routers/trading_sim.py: every endpoint takes
`user: dict = Depends(get_current_user)` and scopes to that user's own
session. Unlike trading_sim.py this genuinely needs Alpaca credentials for
every endpoint that touches an engine (start/state) -- there is no
"degraded but working" mode without historical bars to replay.
"""

import logging
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependency import get_current_user
from app.replay.engine import load_replay_engine

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


@router.patch("/speed")
async def set_speed(body: SpeedRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    _session_or_404(await request.app.state.replay_store.get(user["id"]))
    session = await request.app.state.replay_store.update(user["id"], speed=body.speed)
    return await _state_payload(request, session)


@router.delete("/stop")
async def stop_replay(request: Request, user: dict = Depends(get_current_user)) -> dict:
    await request.app.state.replay_store.stop(user["id"])
    request.app.state.replay_engines.discard(user["id"])
    return {"stopped": True}
