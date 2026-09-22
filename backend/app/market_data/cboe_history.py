"""Price history for Cboe's indices, from the same keyless delayed feed the
header's VIX and 10-year quotes come from (app.market_data.market_conditions).

Two shapes, because that is what Cboe serves:
- intraday: one-minute bars for the most recent session only, stamped in
  New York wall-clock time without a zone;
- daily: one close per day, back to the early nineties.

Used for the chart's 10-year yield markers (app.indicators.ten_year_moves),
which want the minute bars under an intraday or hourly chart and the daily
closes under anything coarser. ^TNX quotes the yield times ten, so every
value that leaves here for it is already in percent.

Best-effort like every other outside source in this app: a failed call is
an empty list -- "not known", which the chart shows as no markers rather
than as an error -- and is not cached, so the next chart load retries.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://cdn.cboe.com/api/global/delayed_quotes/charts"
_HEADERS = {"User-Agent": "trading-dashboard"}
_NY = ZoneInfo("America/New_York")

# The minute bars move every minute; a minute's staleness is what the
# feed's own 15-minute delay already dwarfs. Daily closes change once a day.
INTRADAY_TTL_SECONDS = 60.0
DAILY_TTL_SECONDS = 6 * 60 * 60.0

# Cboe's symbol -> what its quoted number is multiplied by. ^TNX is the
# 10-year yield times ten (49.98 is 4.998 %).
_SCALE = {"_TNX": 0.1}


@dataclass(frozen=True)
class Point:
    at: datetime  # UTC
    value: float


def parse_intraday(body: dict, symbol: str) -> list[Point]:
    """Pure: Cboe's intraday payload -> minute closes in UTC. Its datetimes
    are New York wall-clock with no offset, so they are localised before
    converting -- a naive read as UTC would put every point four or five
    hours early."""
    scale = _SCALE.get(symbol, 1.0)
    points: list[Point] = []
    for row in (body or {}).get("data") or []:
        try:
            local = datetime.fromisoformat(row["datetime"]).replace(tzinfo=_NY)
            close = float(row["price"]["close"])
        except (KeyError, TypeError, ValueError):
            continue
        # The first minute of the day is sometimes all zeros before the
        # index has a print; a zero yield is never a real reading.
        if close <= 0:
            continue
        points.append(Point(local.astimezone(timezone.utc), close * scale))
    return points


def parse_daily(body: dict, symbol: str) -> list[Point]:
    """Pure: Cboe's daily payload -> closes stamped at New York midnight in
    UTC, the same stamp Alpaca's daily bars carry, so a close lands in the
    candle of its own day."""
    scale = _SCALE.get(symbol, 1.0)
    points: list[Point] = []
    for row in (body or {}).get("data") or []:
        try:
            day = datetime.fromisoformat(row["date"]).replace(tzinfo=_NY)
            close = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if close <= 0:
            continue
        points.append(Point(day.astimezone(timezone.utc), close * scale))
    return points


class CboeHistory:
    """Cached, single-flighted fetches of one index's intraday and daily
    history -- every chart load asks, and one request should serve them all."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        symbol: str = "_TNX",
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._symbol = symbol
        self._now = now
        self._cache: dict[str, tuple[float, list[Point]]] = {}
        self._locks = {"intraday": asyncio.Lock(), "historical": asyncio.Lock()}

    async def intraday(self) -> list[Point]:
        return await self._get("intraday", INTRADAY_TTL_SECONDS, parse_intraday)

    async def daily(self) -> list[Point]:
        return await self._get("historical", DAILY_TTL_SECONDS, parse_daily)

    async def _get(self, kind: str, ttl: float, parse) -> list[Point]:
        async with self._locks[kind]:
            cached = self._cache.get(kind)
            if cached is not None and self._now() - cached[0] < ttl:
                return cached[1]
            try:
                resp = await self._client.get(f"{_BASE}/{kind}/{self._symbol}.json", headers=_HEADERS)
                resp.raise_for_status()
                points = parse(resp.json(), self._symbol)
            except Exception:
                logger.warning("Cboe %s history fetch failed for %s", kind, self._symbol, exc_info=True)
                return cached[1] if cached is not None else []
            self._cache[kind] = (self._now(), points)
            return points
