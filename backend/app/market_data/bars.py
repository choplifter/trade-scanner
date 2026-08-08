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


async def get_intraday_minute_bars(clients: AlpacaClients, symbol: str) -> list:
    """Minute bars for the last few trading sessions (premarket open through
    now), used both to render chart history on widget-open and to seed the
    VWAP/EMA accumulators so those lines are continuous instead of starting
    flat.
    """
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=intraday_chart_lookback_start_utc(),
        feed=clients.feed,
        adjustment=Adjustment.RAW,
    )
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    return bar_set.data.get(symbol, [])


async def get_historical_bars(clients: AlpacaClients, symbol: str, timeframe_key: str) -> list:
    """Native-resolution historical bars for one of HISTORICAL_TIMEFRAMES'
    keys (1Hour/4Hour/1Day/1Week/1Month). No VWAP -- it's not a
    same-session concept once a bar spans more than one session.
    """
    alpaca_timeframe, lookback = HISTORICAL_TIMEFRAMES[timeframe_key]
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=alpaca_timeframe,
        start=datetime.now(timezone.utc) - lookback,
        feed=clients.feed,
        adjustment=Adjustment.RAW,
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


async def get_daily_bars_multi(
    clients: AlpacaClients, symbols: list[str], lookback_days: int = 14
) -> dict[str, list]:
    """Daily bars for several symbols in one request -- used to compute
    multi-day continuation context (e.g. "3rd straight up day") for trade
    idea candidates. `lookback_days` is calendar days, not trading days, so
    the default of 14 comfortably covers the ~9-10 trading days needed for a
    5-day-window continuation signal even across a long weekend.
    """
    if not symbols:
        return {}
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=datetime.now(timezone.utc) - timedelta(days=lookback_days),
        feed=clients.feed,
        adjustment=Adjustment.RAW,
    )
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    return dict(bar_set.data)
