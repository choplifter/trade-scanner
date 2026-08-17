"""Screener API -- shared by both front ends.

The React app and the Dash app both build their filter controls and column
pickers from GET /fields rather than listing columns themselves, so a field
added to app.scanners.screener.FIELDS shows up in both with no UI change.
That is the whole reason the registry lives on the server.

The actual screening is delegated to app.scanners.screener_service, which
the Dash page calls directly in-process -- so both surfaces run identical
logic rather than one reimplementing the other.
"""

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.scanners import backtest, screener
from app.scanners.intraday_backtest_runner import run_intraday_backtest
from app.scanners.news_history import catalyst_days, fetch_symbol_news
from app.scanners.screener_service import screen_live_rows
from app.services.market_clock import ET, trading_hours_for

router = APIRouter(prefix="/api/screener", tags=["screener"])


@router.get("/fields")
async def get_fields() -> dict:
    """The screenable field registry: name, label, type and the operators
    valid for that type. Clients render their UI from this.
    """
    return {"fields": screener.fields_payload()}


@router.get("/presets")
async def get_presets() -> dict:
    """Built-in screens, including the three that used to be hardcoded
    views. Returned as full filter specs so a UI can load one and then let
    the user edit it, rather than treating them as opaque names.
    """
    return {"presets": screener.preset_list()}


@router.post("/run")
async def run_screen(screen: screener.Screen, request: Request) -> dict:
    """Run a screen against the current live rows."""
    return screen_live_rows(
        request.app.state.scanner_engine, request.app.state.settings, screen
    )


class BacktestRequest(BaseModel):
    screen: screener.Screen
    lookback_days: int = 180
    horizon_days: int = 1
    max_symbols: int = 300
    # "daily" replays one bar per session (fast, 180d default). "intraday"
    # rebuilds each symbol's state every 5 minutes, which is what makes
    # volume_1h/volume_surge/rvol_1h replayable -- far heavier, so its
    # lookback defaults much shorter.
    resolution: str = "daily"
    # Opt-in: building a historical catalyst map is one FMP request per
    # symbol on a cold cache, so it isn't paid for unless asked. Daily
    # resolution only -- a catalyst is a per-session fact, and the intraday
    # replay would attribute the same story to all 78 bars of a day.
    with_catalysts: bool = False


@router.post("/backtest")
async def backtest_screen(body: BacktestRequest, request: Request) -> dict:
    """Replay a user-built screen over historical daily bars.

    Refuses rather than silently degrading when the screen filters on
    something a daily bar can't reconstruct (float, spread, the intraday
    volume fields -- see backtest.BACKTESTABLE_FIELDS). screener.apply_filters
    ignores unknown fields by design so a stale saved screen still runs live,
    but here that would quietly drop criteria and return a plausible number
    for a strategy the user never described -- the worst failure mode a
    validation tool can have. 422 with the offending field names lets the UI
    say exactly which filters to remove.

    Symbols come from the live universe by dollar volume, the same selection
    scripts/backtest_report.py uses. Note that biases the sample: it's today's
    universe applied to past dates (survivorship), and pre-sorting by dollar
    volume is why most_active reads oddly in the CLI report.
    """
    resolution = body.resolution if body.resolution in backtest.RESOLUTIONS else "daily"
    unsupported = backtest.unsupported_filters(body.screen, resolution)
    if unsupported:
        # Distinguish "switch resolution" from "delete these filters". Telling
        # someone to drop the field they built the screen around, when a
        # 5-minute replay handles it, would be the wrong advice.
        fixable = backtest.intraday_would_help(body.screen) if resolution == "daily" else []
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Switch to intraday resolution to backtest this screen."
                    if fixable
                    else f"This screen can't be backtested at {resolution} resolution."
                ),
                "unsupported_fields": unsupported,
                "retry_with_intraday": fixable,
                "reason": (
                    f"{', '.join(fixable)} need intraday bars -- rerun at intraday resolution."
                    if fixable
                    else (
                        "Spread needs historical quotes nothing fetches, float would be "
                        "today's value applied to past dates, and stale-price describes a "
                        "live feed. No resolution can reconstruct these; remove them to "
                        "backtest the rest."
                    )
                ),
                "backtestable_fields": sorted(backtest.supported_fields(resolution)),
            },
        )

    engine = request.app.state.scanner_engine
    settings = request.app.state.settings
    universe = request.app.state.universe or {}
    if not universe:
        raise HTTPException(status_code=503, detail="Universe not built yet -- try again shortly.")

    ranked = sorted(universe.values(), key=lambda u: u.avg_dollar_vol_20d, reverse=True)
    symbols = [u.symbol for u in ranked[: max(1, body.max_symbols)]]

    # Not a refusal: these fields are replayable, just only against today's
    # values (see backtest.LOOK_AHEAD_FIELDS). The result travels with the
    # warning attached so a biased number can't be read as a validated one.
    look_ahead = backtest.look_ahead_filters(body.screen)

    # Today's float and short interest, keyed by symbol. Flat maps because
    # there is no historical series behind them to key by date -- which is
    # exactly what makes them look-ahead.
    fundamentals = {
        "float_shares": {s: engine.fundamentals.float_shares(s) for s in symbols},
        "short_interest_pct": {s: engine.fundamentals.short_interest_pct(s) for s in symbols},
    }

    catalysts = None
    if body.with_catalysts and resolution == "daily":
        catalysts = await _build_catalyst_map(
            engine, settings, symbols[:_CATALYST_SYMBOL_CAP], body.lookback_days
        )

    if resolution == "intraday":
        return {
            "look_ahead_fields": look_ahead,
            **await run_intraday_backtest(
                engine.clients,
                settings,
                symbols,
                body.screen,
                # A session of 5-minute bars is ~78 rows per symbol per day,
                # so the default 180 would be an enormous pull here. Capped
                # rather than silently honoured.
                lookback_days=min(body.lookback_days, 45),
                fundamentals=fundamentals,
            ),
        }

    return {
        "resolution": "daily",
        "look_ahead_fields": look_ahead,
        **await backtest.run_backtest(
            engine.clients,
            settings,
            symbols,
            lookback_days=body.lookback_days,
            horizon_days=body.horizon_days,
            screen=body.screen,
            fundamentals=fundamentals,
            catalysts=catalysts,
        ),
    }


# A catalyst run costs one request per symbol on a cold cache, so the symbol
# list is capped harder than a plain backtest's. Cached afterwards, keyed by
# symbol and window, so re-running the same measurement is free.
_CATALYST_SYMBOL_CAP = 150


async def _build_catalyst_map(engine, settings, symbols: list[str], lookback_days: int) -> dict:
    """{symbol: {trading_date: headline}} over the backtest window.

    Sessions come from the real NYSE calendar rather than the fetched bars,
    so this doesn't need the bars pulled first and can run concurrently with
    nothing else depending on it.
    """
    end = datetime.now(ET).date()
    start = end - timedelta(days=lookback_days)
    sessions = []
    day = start
    while day <= end:
        if trading_hours_for(day) is not None:
            sessions.append(day)
        day += timedelta(days=1)

    catalysts: dict[str, dict] = {}
    semaphore = asyncio.Semaphore(4)

    async def one(symbol: str) -> None:
        async with semaphore:
            items = await fetch_symbol_news(
                engine.fundamentals.http_client, settings.fmp_api_key, symbol, start, end
            )
        found = catalyst_days(items, sessions)
        if found:
            catalysts[symbol] = found

    await asyncio.gather(*(one(s) for s in symbols))
    return catalysts
