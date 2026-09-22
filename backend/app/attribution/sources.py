"""What else was going on: the outside events a Move Attribution report
lines up against SPY/QQQ moves (see app.attribution.report).

Each source is best-effort and says so -- a missing key or a failed call is
an empty answer plus a note, never an exception, because a report that
knows three of five things is still worth reading:

- Macro releases: FRED's release calendar (CPI, payrolls, GDP, PCE, FOMC
  statements) for the dates, going back decades; FMP's economic calendar
  for the exact minute where the plan reaches (about ten months back).
  FRED lists days only, so the rest get each release's scheduled time --
  08:30 New York for the data, 14:00 for the Fed.
- The 10-year yield and the VIX: Cboe's daily closes (real index values).
- Mega-cap earnings: FMP's per-company earnings history.
- Options expiry: arithmetic on the calendar.

The Analytics app calls these from Dash callbacks, each under its own
asyncio.run (app.dash_app.async_bridge), so nothing here may hold state
tied to an event loop: the caches are plain dicts behind a thread lock and
every fetch takes the caller's own httpx client.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock
from zoneinfo import ZoneInfo

import httpx

from app.market_data.cboe_history import parse_daily
from app.market_data.macro_calendar import macro_events_from_rows

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")

_FRED_BASE = "https://api.stlouisfed.org/fred"
_FMP_BASE = "https://financialmodelingprep.com/stable"
_CBOE_DAILY = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/{symbol}.json"

# FRED release ids -> (label, scheduled New York time). The labels match
# app.market_data.macro_calendar's, so FMP's rows for the same release
# merge onto these rather than appearing twice.
FRED_RELEASES: dict[int, tuple[str, clock]] = {
    10: ("CPI", clock(8, 30)),
    50: ("NFP", clock(8, 30)),
    53: ("GDP", clock(8, 30)),
    54: ("PCE", clock(8, 30)),
    101: ("FOMC", clock(14, 0)),
}

# The index heavyweights whose reports move SPY and QQQ on their own --
# together roughly a third of SPY and half of QQQ.
MEGA_CAPS: tuple[str, ...] = ("NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL", "AVGO", "TSLA")

_TTL_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class Release:
    label: str
    at: datetime  # UTC
    # False when the minute is the release's scheduled time rather than a
    # published one (every FRED-only date).
    exact: bool


class _Cache:
    """Values by key with a time-to-live, safe to share across the worker
    threads Dash callbacks run on."""

    def __init__(self, ttl: float = _TTL_SECONDS) -> None:
        self._ttl = ttl
        self._values: dict = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._values.get(key)
            if hit is None or time.monotonic() - hit[0] > self._ttl:
                return None
            return hit[1]

    def put(self, key, value) -> None:
        with self._lock:
            self._values[key] = (time.monotonic(), value)


_cache = _Cache()


def _ny_utc(day: date, at: clock) -> datetime:
    return datetime.combine(day, at, tzinfo=NY).astimezone(timezone.utc)


# --- macro releases ------------------------------------------------------


def parse_fred_release_dates(body: dict, release_id: int) -> list[Release]:
    """Pure: FRED's /release/dates payload -> releases at their scheduled
    New York time."""
    label, at = FRED_RELEASES[release_id]
    out = []
    for row in (body or {}).get("release_dates") or []:
        try:
            day = date.fromisoformat(row["date"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(Release(label, _ny_utc(day, at), exact=False))
    return out


async def fred_releases(client: httpx.AsyncClient, api_key: str) -> tuple[list[Release], str | None]:
    """Every tracked release FRED knows of since 2010. (releases, note)."""
    if not api_key:
        return [], "No FRED_API_KEY in backend/.env: releases before FMP's window are unknown."
    cached = _cache.get("fred")
    if cached is not None:
        return cached, None
    releases: list[Release] = []
    for release_id in FRED_RELEASES:
        try:
            resp = await client.get(
                f"{_FRED_BASE}/release/dates",
                params={
                    "release_id": release_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "realtime_start": "2010-01-01",
                    "realtime_end": "9999-12-31",
                    "include_release_dates_with_no_data": "false",
                    "sort_order": "asc",
                    "limit": 10000,
                },
            )
            resp.raise_for_status()
            releases.extend(parse_fred_release_dates(resp.json(), release_id))
        except Exception:
            logger.warning("FRED release dates failed for release %s", release_id, exc_info=True)
            return [], "FRED did not answer; releases before FMP's window are unknown for this run."
    _cache.put("fred", releases)
    return releases, None


async def fmp_releases(
    client: httpx.AsyncClient, api_key: str, start: date, end: date
) -> tuple[list[Release], date | None]:
    """The same releases with their published minute, as far back as the
    plan allows -- asked newest quarter first, stopping at the first one
    the plan refuses (HTTP 402) rather than asking for every older one.
    Also returns the first day covered (None if nothing was)."""
    if not api_key:
        return [], None
    out: list[Release] = []
    covered_from: date | None = None
    chunk_end = end
    while chunk_end >= start:
        chunk_start = max(start, chunk_end - timedelta(days=89))
        key = ("fmp", chunk_start, chunk_end)
        rows = _cache.get(key)
        if rows is None:
            try:
                resp = await client.get(
                    f"{_FMP_BASE}/economic-calendar",
                    params={"from": chunk_start.isoformat(), "to": chunk_end.isoformat(), "apikey": api_key},
                )
                if resp.status_code == 402:
                    break
                resp.raise_for_status()
                rows = resp.json()
            except Exception:
                logger.warning("FMP economic calendar %s..%s failed", chunk_start, chunk_end, exc_info=True)
                break
            _cache.put(key, rows)
        covered_from = chunk_start
        for event in macro_events_from_rows(rows, chunk_start, chunk_end):
            if event.at is not None:
                out.append(Release(event.label, event.at, exact=True))
        chunk_end = chunk_start - timedelta(days=1)
    return out, covered_from


def merge_releases(fred: list[Release], fmp: list[Release]) -> list[Release]:
    """FRED's dates, with FMP's exact minute wherever both list the same
    release on the same New York day; FMP-only releases kept too."""
    exact = {(r.label, r.at.astimezone(NY).date()): r for r in fmp}
    merged = {}
    for r in fred:
        key = (r.label, r.at.astimezone(NY).date())
        merged[key] = exact.get(key, r)
    for key, r in exact.items():
        merged.setdefault(key, r)
    return sorted(merged.values(), key=lambda r: r.at)


# --- Cboe daily closes ---------------------------------------------------


async def cboe_daily_closes(client: httpx.AsyncClient, symbol: str) -> dict[date, float]:
    """New York date -> close, for a Cboe index ("_TNX" in percent, "_VIX")."""
    key = ("cboe", symbol)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    try:
        resp = await client.get(_CBOE_DAILY.format(symbol=symbol), headers={"User-Agent": "trading-dashboard"})
        resp.raise_for_status()
        points = parse_daily(resp.json(), symbol)
    except Exception:
        logger.warning("Cboe daily history failed for %s", symbol, exc_info=True)
        return {}
    closes = {p.at.astimezone(NY).date(): p.value for p in points}
    _cache.put(key, closes)
    return closes


# --- earnings ------------------------------------------------------------


async def megacap_earnings(client: httpx.AsyncClient, api_key: str) -> dict[date, list[str]]:
    """Report date -> the mega-caps reporting that day. FMP gives the day but
    not whether before the open or after the close, so the report treats
    both that session and the next as the reaction."""
    if not api_key:
        return {}
    cached = _cache.get("earnings")
    if cached is not None:
        return cached
    by_day: dict[date, list[str]] = {}
    for symbol in MEGA_CAPS:
        try:
            resp = await client.get(f"{_FMP_BASE}/earnings", params={"symbol": symbol, "limit": 100, "apikey": api_key})
            resp.raise_for_status()
            rows = resp.json()
        except Exception:
            logger.warning("FMP earnings history failed for %s", symbol, exc_info=True)
            continue
        for row in rows if isinstance(rows, list) else []:
            try:
                day = date.fromisoformat(str(row.get("date"))[:10])
            except ValueError:
                continue
            by_day.setdefault(day, []).append(symbol)
    _cache.put("earnings", by_day)
    return by_day


# --- options expiry ------------------------------------------------------


def opex_label(day: date) -> str | None:
    """"Quad witching" on the third Friday of March, June, September and
    December, "OPEX" on the other months' third Friday. Holiday shifts (a
    Good Friday expiry moving to Thursday) are not modelled."""
    if day.weekday() != 4 or not 15 <= day.day <= 21:
        return None
    return "Quad witching" if day.month in (3, 6, 9, 12) else "OPEX"
