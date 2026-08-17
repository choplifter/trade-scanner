import asyncio
from datetime import datetime, timedelta, timezone

from alpaca.data.enums import Adjustment
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from app.alpaca.client import AlpacaClients
from app.services.market_clock import ET, PREMARKET_START, _NYSE

# Timeframes beyond 1-minute intraday are fetched at their native Alpaca
# resolution over a lookback long enough to fill a chart, rather than
# aggregated client-side from 1-minute bars (which would mean fetching and
# shipping tens of thousands of bars for a monthly view). Shared by the
# REST bars endpoint (routers/symbols.py) and the Dash symbol-detail page.
HISTORICAL_TIMEFRAMES: dict[str, tuple[TimeFrame, timedelta]] = {
    "1Hour": (TimeFrame(1, TimeFrameUnit.Hour), timedelta(days=60)),
    "4Hour": (TimeFrame(4, TimeFrameUnit.Hour), timedelta(days=200)),
    "1Day": (TimeFrame(1, TimeFrameUnit.Day), timedelta(days=730)),
    "1Week": (TimeFrame(1, TimeFrameUnit.Week), timedelta(days=365 * 5)),
    "1Month": (TimeFrame(1, TimeFrameUnit.Month), timedelta(days=365 * 10)),
}

# How many trading sessions the 1m/5m/15m chart looks back over. >1 so the
# chart isn't nearly empty in the early minutes of a session (or before
# premarket even opens); large enough that 5m/15m still have real scrollable
# depth once bucketed (10 sessions of 1-min bars -> ~250-800 bars at 5m/15m),
# and still bounded so payloads stay reasonable -- higher timeframes (1h/4h/
# D/W/M) cover longer history natively instead of client-side aggregation.
_INTRADAY_SESSION_LOOKBACK = 10


def today_premarket_start_utc(now: datetime | None = None) -> datetime:
    now = (now or datetime.now(ET)).astimezone(ET)
    start_et = datetime.combine(now.date(), PREMARKET_START, tzinfo=ET)
    return start_et.astimezone(timezone.utc)


def intraday_chart_lookback_start_utc(
    now: datetime | None = None, sessions: int = _INTRADAY_SESSION_LOOKBACK
) -> datetime:
    """Start of the Nth-most-recent trading session's premarket open, in UTC.

    Unlike `today_premarket_start_utc`, this always resolves to a timestamp
    in the past: walking back over the last two weeks of the NYSE calendar
    and only keeping sessions whose premarket has actually started avoids
    the case where "today" hasn't opened yet (e.g. it's 2am ET) and a naive
    "today at 4am" start would land after `now`, which the Alpaca API
    rejects with "end should not be before start".
    """
    now = (now or datetime.now(ET)).astimezone(ET)
    schedule = _NYSE.schedule(start_date=now.date() - timedelta(days=14), end_date=now.date())
    started_sessions = [
        day.date()
        for day in schedule.index
        if datetime.combine(day.date(), PREMARKET_START, tzinfo=ET) <= now
    ]
    if started_sessions:
        session_day = started_sessions[-min(sessions, len(started_sessions))]
    else:
        session_day = now.date()
    start_et = datetime.combine(session_day, PREMARKET_START, tzinfo=ET)
    return start_et.astimezone(timezone.utc)


# Native Alpaca resolution the momentum alarm computes its 15m%/VWAP/
# shape-and-color confirmation from -- see app.scanners.momentum_cache and
# app.scanners.momentum_backtest. Was 1-minute bars; a single 1-minute
# print is noisy, and 5-minute is the smallest size that still reads as a
# real, chart-recognizable candle for the shape/color check (matches this
# app's own chart default, see lightweight_chart.html's DEFAULT_KEY).
# Fetched natively at this resolution (not aggregated client-side from
# 1-minute bars) so it's also ~5x fewer bars over the wire. Exposed as a
# plain int too (not just baked into the TimeFrame object below) so
# app.scanners.momentum_backtest can convert its own wall-clock-minutes
# `horizon_minutes` parameter into a bar-count offset without a second,
# possibly-drifting "5" hardcoded elsewhere.
MOMENTUM_BAR_MINUTES = 5
MOMENTUM_BAR_TIMEFRAME = TimeFrame(MOMENTUM_BAR_MINUTES, TimeFrameUnit.Minute)


async def get_intraday_minute_bars(clients: AlpacaClients, symbol: str, start: datetime | None = None) -> list:
    """Minute bars for the last few trading sessions (premarket open through
    now) by default, used both to render chart history on widget-open and
    to seed the VWAP/EMA accumulators so those lines are continuous instead
    of starting flat. `start` overrides that default -- e.g. to reach back
    to a specific historical session instead (see routers/symbols.py's
    `around` query param, used when a Dash backtest pick's chart needs to
    show a moment further back than the default window covers).
    """
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start or intraday_chart_lookback_start_utc(),
        feed=clients.feed,
        adjustment=Adjustment.RAW,
    )
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    return bar_set.data.get(symbol, [])


async def get_historical_bars(clients: AlpacaClients, symbol: str, timeframe_key: str) -> list:
    """Native-resolution historical bars for one of HISTORICAL_TIMEFRAMES'
    keys (1Hour/4Hour/1Day/1Week/1Month). No VWAP -- it's not a
    same-session concept once a bar spans more than one session.

    Split-adjusted, unlike the intraday fetches above. These windows run to
    two years, long enough to span corporate actions, and unadjusted prices
    across one are not a chart -- they are two charts drawn on one axis.
    IPST is the case that surfaced it: two 1-for-20 reverse splits inside a
    year (2025-11-05 and 2026-04-23) put ~19x vertical cliffs in the daily
    view, 0.284 to 5.33 overnight, with every bar before the older split
    reading 400x too low. Nothing about that is a price move.

    The intraday fetches stay RAW deliberately: they cover a few sessions,
    so a split inside the window is rare, and the live websocket pushes
    unadjusted trades -- adjusting the backfill but not the stream would put
    a step at the join between them. get_daily_bars_multi and
    get_5m_bars_multi already use SPLIT for the same reason this one now
    does.
    """
    alpaca_timeframe, lookback = HISTORICAL_TIMEFRAMES[timeframe_key]
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=alpaca_timeframe,
        start=datetime.now(timezone.utc) - lookback,
        feed=clients.feed,
        adjustment=Adjustment.SPLIT,
    )
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    return bar_set.data.get(symbol, [])


async def get_intraday_minute_bars_multi(
    clients: AlpacaClients, symbols: list[str]
) -> dict[str, list]:
    """Same as `get_intraday_minute_bars` but for several symbols in one
    request -- used to compute session VWAP for a batch of scanner
    candidates without firing one HTTP call per symbol. Only looks back 1
    session: SessionVwapState resets on every calendar-date change anyway,
    so extra prior days would just be replayed and discarded, and trade-idea
    generation only cares about *today's* VWAP.
    """
    if not symbols:
        return {}
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Minute,
        start=intraday_chart_lookback_start_utc(sessions=1),
        feed=clients.feed,
        adjustment=Adjustment.RAW,
    )
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    return dict(bar_set.data)


async def get_intraday_5m_bars_multi(clients: AlpacaClients, symbols: list[str]) -> dict[str, list]:
    """Same as get_intraday_minute_bars_multi but at MOMENTUM_BAR_TIMEFRAME
    (5-minute) resolution -- feeds the live momentum alarm (see
    app.scanners.momentum_cache), not the AI-trade-idea VWAP/momentum
    context, which stays on 1-minute bars via get_intraday_minute_bars_multi.
    """
    if not symbols:
        return {}
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=MOMENTUM_BAR_TIMEFRAME,
        start=intraday_chart_lookback_start_utc(sessions=1),
        feed=clients.feed,
        adjustment=Adjustment.RAW,
    )
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    return dict(bar_set.data)


async def get_daily_bars_multi(
    clients: AlpacaClients, symbols: list[str], lookback_days: int = 14
) -> dict[str, list]:
    """Daily bars for several symbols in one request -- used to compute
    multi-day continuation context (e.g. "3rd straight up day") for trade
    idea candidates, and as the closed-market scanner fallback's source of
    prev_close/last_close (see app.scanners.latest_session). `lookback_days`
    is calendar days, not trading days, so the default of 14 comfortably
    covers the ~9-10 trading days needed for a 5-day-window continuation
    signal even across a long weekend.

    adjustment=SPLIT (not the RAW every other bars call here uses): unlike
    the live snapshot endpoint (StockSnapshotRequest, no adjustment option
    at all -- see ScannerEngine._refresh_split_ratios for how that path is
    corrected instead), the historical bars endpoint can just be asked for
    already-adjusted closes directly. Without this, a stock that reverse-
    split shows a nonsensical multi-thousand-percent single-day "move"
    here whenever the split falls inside the lookback window -- both for
    the scanner fallback's gap% and for trade-idea continuation signals
    (a split would otherwise look like an enormous, fake "up" or "down"
    day). Deliberately SPLIT, not ALL: a dividend's ex-date price dip is a
    real traded price, not a share-count artifact, so it shouldn't be
    smoothed away here the way a split's share-count discontinuity should.
    """
    if not symbols:
        return {}
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=datetime.now(timezone.utc) - timedelta(days=lookback_days),
        feed=clients.feed,
        adjustment=Adjustment.SPLIT,
    )
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    return dict(bar_set.data)


async def get_5m_bars_multi(
    clients: AlpacaClients, symbols: list[str], lookback_days: int = 30
) -> dict[str, list]:
    """MOMENTUM_BAR_TIMEFRAME (5-minute) bars for several symbols over an
    explicit multi-week/month calendar-day lookback -- unlike
    get_intraday_5m_bars_multi (hard-coded to 1 session, for seeding
    today's live momentum alarm), this is for historical backtesting (see
    app.scanners.momentum_backtest) over weeks of history. Alpaca
    paginates internally for a range this large; a multi-week,
    multi-hundred-symbol call can take real wall-clock time (potentially
    minutes, not seconds) -- see app.scanners.bar_cache for why callers
    doing this more than once should go through its disk cache instead of
    calling this directly every time.

    adjustment=SPLIT, same reasoning as get_daily_bars_multi: a split
    inside the lookback window would otherwise show as a nonsensical
    single-bar price discontinuity.
    """
    if not symbols:
        return {}
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=MOMENTUM_BAR_TIMEFRAME,
        start=datetime.now(timezone.utc) - timedelta(days=lookback_days),
        feed=clients.feed,
        adjustment=Adjustment.SPLIT,
    )
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    return dict(bar_set.data)
