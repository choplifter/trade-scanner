"""Fetching and assembling a Move Attribution report -- the I/O around the
pure logic in app.attribution.report. Called from the Analytics page under
app.dash_app.async_bridge.run_async, so it opens its own HTTP client per
run and keeps nothing tied to an event loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from alpaca.data.enums import Adjustment
from alpaca.data.requests import NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.common.enums import Sort

from app.alpaca.client import AlpacaClients
from app.attribution import sources
from app.attribution.report import (
    Candle,
    Report,
    attribute,
    daily_rows,
    factor_stats,
    intraday_rows,
    yield_opposite,
    yield_threshold_bp,
)
from app.core.config import Settings

logger = logging.getLogger(__name__)

# Bars are the slow part (months of 5-minute bars page through Alpaca), and
# someone changing only the threshold should not wait for them again.
_BARS_TTL_SECONDS = 10 * 60
_bars_cache: dict = {}
_bars_lock = threading.Lock()

# Twenty sessions of warm-up so the first days of the window already have a
# spread to be measured against.
_DAILY_WARMUP = timedelta(days=45)
_INTRADAY_WARMUP = timedelta(days=30)


async def _bars(clients: AlpacaClients, symbols: list[str], timeframe: TimeFrame, start: datetime) -> dict[str, list]:
    key = (tuple(symbols), str(timeframe), start.date())
    with _bars_lock:
        hit = _bars_cache.get(key)
        if hit and time.monotonic() - hit[0] < _BARS_TTL_SECONDS:
            return hit[1]
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=timeframe,
        start=start,
        feed=clients.feed,
        adjustment=Adjustment.SPLIT,
    )
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    data = dict(bar_set.data)
    with _bars_lock:
        _bars_cache[key] = (time.monotonic(), data)
    return data


def _candles(bars: list) -> list[Candle]:
    return [Candle(at=b.timestamp, open=float(b.open), close=float(b.close)) for b in bars]


async def build_report(
    clients: AlpacaClients,
    settings: Settings,
    symbol: str,
    *,
    mode: str,
    candle_minutes: int = 15,
    lookback_days: int = 365 * 3,
    k: float = 2.5,
) -> Report:
    """mode "daily" (lookback in days, typically years) or "intraday"
    (5 or 15-minute candles, lookback in days, typically weeks)."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=lookback_days)
    notes: list[str] = []

    if mode == "daily":
        data = await _bars(clients, [symbol], TimeFrame.Day, start - _DAILY_WARMUP)
        rows = daily_rows(_candles(data.get(symbol, [])), k)
        ief = None
    else:
        data = await _bars(
            clients, [symbol, "IEF"], TimeFrame(candle_minutes, TimeFrameUnit.Minute), start - _INTRADAY_WARMUP
        )
        rows = intraday_rows(_candles(data.get(symbol, [])), candle_minutes, k)
        ief = {b.timestamp: float(b.close) for b in data.get("IEF", [])}
        notes.append("The 10Y column is estimated from IEF (the 7-10 year Treasury ETF, ~7 years duration) -- an approximation of the yield move, not the index.")
    # The warm-up was only there to give the first candles a spread.
    rows = [r for r in rows if r.at >= start]
    if not rows:
        return Report(symbol, mode, candle_minutes, [], [], (0, 0), notes + ["No bars came back for this window."])

    async with httpx.AsyncClient(timeout=20.0) as client:
        (fred, fred_note), (fmp, fmp_from), earnings, ten_year, vix = await asyncio.gather(
            sources.fred_releases(client, settings.fred_api_key),
            sources.fmp_releases(client, settings.fmp_api_key, rows[0].at.date(), now.date()),
            sources.megacap_earnings(client, settings.fmp_api_key),
            sources.cboe_daily_closes(client, "_TNX") if mode == "daily" else _none(),
            sources.cboe_daily_closes(client, "_VIX") if mode == "daily" else _none(),
        )
    if fred_note:
        notes.append(fred_note)
    releases = sources.merge_releases(fred, fmp)
    # Where release data starts: FRED reaches back to 2010, so with it
    # everything is covered; without it only FMP's window is.
    known_from = None
    if not fred:
        known_from = datetime.combine(fmp_from, datetime.min.time(), tzinfo=timezone.utc) if fmp_from else now
        if known_from > rows[0].at:
            notes.append(
                f"Releases are only known from {known_from.date():%Y-%m-%d}; before that the Release column says "
                "'unknown' and the release factors below are measured over the known stretch only."
            )
    if not earnings:
        notes.append("Mega-cap earnings dates were unavailable (FMP).")
    if mode == "daily" and not ten_year:
        notes.append("The 10-year history from Cboe was unavailable.")

    attribute(
        rows,
        mode=mode,
        candle_minutes=candle_minutes,
        releases=releases,
        earnings=earnings,
        ten_year=ten_year or None,
        vix=vix or None,
        ief=ief,
        releases_known_from=known_from,
    )
    threshold = yield_threshold_bp(candle_minutes if mode == "intraday" else 390)
    return Report(
        symbol=symbol,
        mode=mode,
        candle_minutes=candle_minutes,
        rows=rows,
        stats=factor_stats(rows),
        yield_opposite=yield_opposite(rows, threshold),
        notes=notes,
    )


async def _none():
    return {}


# Words that mark a story as market-wide rather than one company's -- the
# kind that moves an index and usually carries no ticker. A heuristic, and
# labelled as one on the page: it finds "Fed", not every story that matters.
MACRO_HEADLINE = re.compile(
    r"\b(fed|fomc|powell|rate (cut|hike)s?|interest rates?|inflation|cpi|pce|payrolls?|jobs report|"
    r"unemployment|gdp|recession|treasur(y|ies)|yields?|bond market|tariffs?|trade (war|deal)|trump|"
    r"white house|congress|shutdown|debt ceiling|china|beijing|iran|israel|russia|ukraine|war|ceasefire|"
    r"sanctions?|oil|opec|crude|dollar|ecb|boj|bank of japan|geopolitic\w*|futures|s&p 500|nasdaq|dow)\b",
    re.IGNORECASE,
)


def is_macro_headline(headline: str) -> bool:
    return bool(MACRO_HEADLINE.search(headline or ""))


async def headlines(
    clients: AlpacaClients, symbols: list[str] | None, start: datetime, end: datetime, limit: int = 20
) -> list[dict]:
    """Alpaca/Benzinga headlines tagged with any of `symbols` in the window,
    or every headline when `symbols` is None -- the macro and geopolitical
    stories that move an index often carry no ticker at all. Newest first.
    Best-effort: [] on failure."""
    try:
        request = NewsRequest(
            symbols=",".join(symbols) if symbols else None, start=start, end=end, sort=Sort.DESC, limit=limit
        )
        news_set = await asyncio.to_thread(clients.news.get_news, request)
    except Exception:
        logger.warning("Headlines for %s..%s failed", start, end, exc_info=True)
        return []
    return [
        {
            "at": a.created_at,
            "headline": a.headline,
            "source": a.source,
            "symbols": list(a.symbols or []),
            "url": a.url,
        }
        for a in news_set.data.get("news", [])
    ]
