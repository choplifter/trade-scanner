"""How tradable SPY/QQQ are in the European morning -- the measurement
behind "can we trade around the DAX open at 09:00 German time?".

Alpaca's 24/5 trading opens US stocks and ETFs overnight, 20:00-04:00 New
York, which is 02:00-10:00 in Germany: the DAX opens inside it. Whether
that is worth anything depends on what the overnight market looks like
there, so this measures it, per trading day and per window:

- how many of the window's minutes traded at all (a minute bar exists),
- volume and dollar volume,
- the quoted spread -- what a round trip costs -- as the median of
  samples every five minutes (the last quote in a ten-second slice), in
  cents and in basis points of price. Sampled because market makers
  requote constantly even when nothing trades: a single overnight hour of
  SPY runs past 20,000 quotes, and fetching them all only ever reached the
  window's first minutes,
- the range and the net move, and the range in spreads: how many round
  trips' worth the price typically travels, which is what decides whether
  a move can be traded at all after costs.

The windows are wall-clock German time, converted per date through the
real time zones (the US and Europe change clocks on different weekends).
Overnight windows read Alpaca's Blue Ocean feed ("boats"); the premarket
and regular-session ones read the app's normal feed. The regular session's
first hour is there for scale; its spread is not measured -- SPY's is a
cent, and hours of regular-session quotes are too many to fetch for that.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest, StockQuotesRequest
from alpaca.data.timeframe import TimeFrame

from app.services.market_clock import trading_hours_for

logger = logging.getLogger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

# Spread samples: every SAMPLE_EVERY across the window, the last quote in
# the SAMPLE_SLICE after it. At most SAMPLE_CONCURRENCY requests in flight.
SAMPLE_EVERY = timedelta(minutes=5)
SAMPLE_SLICE = timedelta(seconds=10)
SAMPLE_QUOTES = 200
SAMPLE_CONCURRENCY = 8


@dataclass(frozen=True)
class Window:
    key: str
    label: str
    start: time  # German wall clock
    end: time
    spread: bool = True


WINDOWS: tuple[Window, ...] = (
    Window("pre_dax", "08:00-09:00 (before the DAX)", time(8, 0), time(9, 0)),
    Window("dax_open", "09:00-10:00 (DAX open)", time(9, 0), time(10, 0)),
    Window("us_pre", "10:00-11:00", time(10, 0), time(11, 0)),
    Window("us_open", "15:30-16:30 (US open)", time(15, 30), time(16, 30), spread=False),
)

NY = ZoneInfo("America/New_York")


def is_overnight(at: datetime) -> bool:
    """Alpaca's overnight session, 20:00-04:00 New York. Decided per date
    rather than per window: for a few weeks each spring and autumn only one
    side of the Atlantic has changed its clocks, and 09:00 in Germany is
    then 04:00 in New York -- US premarket, on the regular feed."""
    local = at.astimezone(NY).time()
    return local < time(4, 0) or local >= time(20, 0)


def window_utc(day: date, window: Window) -> tuple[datetime, datetime]:
    """The window on a German calendar date, in UTC."""
    start = datetime.combine(day, window.start, tzinfo=BERLIN).astimezone(timezone.utc)
    end = datetime.combine(day, window.end, tzinfo=BERLIN).astimezone(timezone.utc)
    return start, end


def recent_trading_days(count: int, today: date | None = None) -> list[date]:
    """The last `count` NYSE trading days before today, oldest first. Today
    is left out: its windows may not have happened yet."""
    day = (today or datetime.now(BERLIN).date()) - timedelta(days=1)
    days: list[date] = []
    while len(days) < count:
        if trading_hours_for(day) is not None:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


@dataclass
class WindowStats:
    symbol: str
    day: date
    window: str
    minutes: int
    minutes_traded: int
    volume: float
    dollar_volume: float
    range_pct: float | None
    move_pct: float | None
    spread_cents: float | None
    spread_bps: float | None
    samples: int

    @property
    def coverage(self) -> float:
        return self.minutes_traded / self.minutes if self.minutes else 0.0

    @property
    def range_in_spreads(self) -> float | None:
        if self.range_pct is None or not self.spread_bps:
            return None
        return self.range_pct * 100 / self.spread_bps


def bar_stats(bars: list, start: datetime, end: datetime) -> dict:
    """Pure: the minute bars inside [start, end) -> counts, volume, range, move."""
    inside = [b for b in bars if start <= b.timestamp < end]
    minutes = int((end - start).total_seconds() // 60)
    if not inside:
        return {"minutes": minutes, "minutes_traded": 0, "volume": 0.0, "dollar_volume": 0.0, "range_pct": None, "move_pct": None}
    first_open = float(inside[0].open)
    high = max(float(b.high) for b in inside)
    low = min(float(b.low) for b in inside)
    return {
        "minutes": minutes,
        "minutes_traded": len(inside),
        "volume": sum(float(b.volume) for b in inside),
        "dollar_volume": sum(float(b.volume) * float(b.vwap or b.close) for b in inside),
        "range_pct": (high - low) / first_open * 100 if first_open else None,
        "move_pct": (float(inside[-1].close) / first_open - 1) * 100 if first_open else None,
    }


def spread_stats(quotes: list, start: datetime, end: datetime) -> tuple[float | None, float | None]:
    """Pure: the median over the window's minutes of the last two-sided
    quote in each minute -- per minute rather than per quote, so a burst of
    updates in one minute does not outvote a quiet stretch. Fed the sampled
    quotes, that is one vote per sample. (cents, bps)"""
    last_per_minute: dict[int, tuple[float, float]] = {}
    for q in quotes:
        at = q.timestamp
        if not (start <= at < end):
            continue
        bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
        if bid <= 0 or ask <= 0 or ask < bid:
            continue
        last_per_minute[int((at - start).total_seconds() // 60)] = (bid, ask)
    if not last_per_minute:
        return None, None
    cents = [(ask - bid) * 100 for bid, ask in last_per_minute.values()]
    bps = [(ask - bid) / ((ask + bid) / 2) * 10_000 for bid, ask in last_per_minute.values()]
    return statistics.median(cents), statistics.median(bps)


@dataclass
class Measurement:
    rows: list[WindowStats] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


async def _bars(clients, symbols: list[str], start: datetime, end: datetime, feed: DataFeed) -> dict[str, list]:
    request = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Minute, start=start, end=end, feed=feed)
    bar_set = await asyncio.to_thread(clients.data.get_stock_bars, request)
    return dict(bar_set.data)


async def _quotes(clients, symbol: str, start: datetime, end: datetime, feed: DataFeed) -> list:
    request = StockQuotesRequest(symbol_or_symbols=symbol, start=start, end=end, feed=feed, limit=SAMPLE_QUOTES)
    quote_set = await asyncio.to_thread(clients.data.get_stock_quotes, request)
    return list(quote_set.data.get(symbol, []))


def sample_times(start: datetime, end: datetime) -> list[datetime]:
    times, at = [], start
    while at < end:
        times.append(at)
        at += SAMPLE_EVERY
    return times


async def _sampled_quotes(clients, symbol: str, start: datetime, end: datetime, feed: DataFeed, gate) -> list:
    """The last quote of each sample slice across the window."""

    async def one(at: datetime):
        async with gate:
            quotes = await _quotes(clients, symbol, at, at + SAMPLE_SLICE, feed)
        two_sided = [q for q in quotes if q.bid_price and q.ask_price]
        return two_sided[-1] if two_sided else None

    picked = await asyncio.gather(*(one(at) for at in sample_times(start, end)))
    return [q for q in picked if q is not None]


async def measure(clients, symbols: list[str], days: int = 10) -> Measurement:
    result = Measurement()
    trading_days = recent_trading_days(days)
    span_start = window_utc(trading_days[0], WINDOWS[0])[0] - timedelta(hours=1)
    span_end = window_utc(trading_days[-1], WINDOWS[-1])[1] + timedelta(hours=1)
    normal_feed = clients.feed

    gate = asyncio.Semaphore(SAMPLE_CONCURRENCY)
    feeds_ok = {}
    bars_by_feed: dict[str, dict[str, list]] = {}
    for name, feed in (("overnight", DataFeed.BOATS), ("normal", normal_feed)):
        try:
            bars_by_feed[name] = await _bars(clients, symbols, span_start, span_end, feed)
            feeds_ok[name] = True
        except Exception as exc:
            logger.warning("Overnight measurement: %s bars failed", name, exc_info=True)
            bars_by_feed[name] = {}
            feeds_ok[name] = False
            result.notes.append(f"{'Blue Ocean (overnight)' if name == 'overnight' else 'Regular'} bars unavailable: {exc}")

    for symbol in symbols:
        for day in trading_days:
            for window in WINDOWS:
                start, end = window_utc(day, window)
                overnight = is_overnight(start)
                feed_name = "overnight" if overnight else "normal"
                stats = bar_stats(bars_by_feed[feed_name].get(symbol, []), start, end)
                spread_cents = spread_bps = None
                sample_count = 0
                if window.spread and feeds_ok.get(feed_name):
                    try:
                        quotes = await _sampled_quotes(
                            clients, symbol, start, end, DataFeed.BOATS if overnight else normal_feed, gate
                        )
                        sample_count = len(quotes)
                        spread_cents, spread_bps = spread_stats(quotes, start, end)
                    except Exception as exc:
                        logger.warning("Overnight measurement: quotes failed for %s %s", symbol, day, exc_info=True)
                        note = f"Quotes unavailable ({window.label}): {exc}"
                        if note not in result.notes:
                            result.notes.append(note)
                result.rows.append(
                    WindowStats(
                        symbol=symbol,
                        day=day,
                        window=window.key,
                        spread_cents=spread_cents,
                        spread_bps=spread_bps,
                        samples=sample_count,
                        **stats,
                    )
                )
    return result


def summarize(rows: list[WindowStats]) -> list[dict]:
    """Per symbol and window, the median day."""
    out = []
    for symbol in sorted({r.symbol for r in rows}):
        for window in WINDOWS:
            group = [r for r in rows if r.symbol == symbol and r.window == window.key]
            if not group:
                continue

            def med(values):
                values = [v for v in values if v is not None]
                return statistics.median(values) if values else None

            out.append(
                {
                    "symbol": symbol,
                    "window": window.label,
                    "days": len(group),
                    "coverage": med([r.coverage for r in group]),
                    "volume": med([r.volume for r in group]),
                    "dollar_volume": med([r.dollar_volume for r in group]),
                    "spread_cents": med([r.spread_cents for r in group]),
                    "spread_bps": med([r.spread_bps for r in group]),
                    "range_pct": med([r.range_pct for r in group]),
                    "abs_move_pct": med([abs(r.move_pct) if r.move_pct is not None else None for r in group]),
                    "range_in_spreads": med([r.range_in_spreads for r in group]),
                }
            )
    return out
