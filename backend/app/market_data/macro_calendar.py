"""The scheduled US macro releases that move index options: FOMC, CPI,
payrolls, PCE, GDP. Where app.market_data.market_conditions asks FMP's
economic calendar about *today* to colour the market-conditions light,
this asks about the coming weeks so an expiry strip can say which
contracts are held through a release.

Same source (FMP /economic-calendar, the key the app already has), same
best-effort posture: without a key or on a failed call the answer is an
empty list, which callers must read as "not known", never as "nothing
scheduled". Filtered by name rather than by FMP's impact flag alone: the
flag marks weekly jobless claims "High" too, and a strip dotted every
Thursday says nothing.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

_FMP_BASE = "https://financialmodelingprep.com/stable"
_TIMEOUT = httpx.Timeout(10.0)

# How far ahead the strip looks: the chain cache holds 60 days of expiries.
WINDOW_DAYS = 70
# Release schedules are published months ahead; a few hours is plenty.
CACHE_TTL_SECONDS = 6 * 60 * 60

# Patterns over FMP's lower-cased event names, each with the label the strip
# shows. Anchored on purpose: FMP also lists "Cleveland CPI", "FOMC Minutes",
# "FOMC Economic Projections", "Nonfarm Payrolls Private" and "U-6
# Unemployment Rate", none of which is the release the day is known for.
# The unemployment rate is not tracked separately: it lands with payrolls.
MACRO_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^fed interest rate decision"), "FOMC"),
    (re.compile(r"^(core )?cpi\b"), "CPI"),
    (re.compile(r"^non[- ]?farm payrolls(?! private)"), "NFP"),
    (re.compile(r"^(core )?pce price index"), "PCE"),
    (re.compile(r"^gdp growth rate qoq"), "GDP"),
)


@dataclass(frozen=True)
class MacroEvent:
    date: date
    label: str
    event: str
    # The release moment in UTC, as FMP lists it (08:30 New York CPI is
    # 12:30 or 13:30 UTC). None only when FMP gave a bare date. Not part of
    # to_dict: the expiry strip is by day and its payload stays as it was;
    # the chart's release markers (app.indicators.macro_releases) read it.
    # Left out of equality: a release is its day and name.
    at: datetime | None = field(default=None, compare=False)

    def to_dict(self) -> dict:
        return {"date": self.date.isoformat(), "label": self.label, "event": self.event}


def macro_label(event_name: str) -> str | None:
    """The short label for an FMP event name, or None when it is not one of
    the releases tracked here."""
    name = (event_name or "").strip().lower()
    for pattern, label in MACRO_PATTERNS:
        if pattern.search(name):
            return label
    return None


def macro_events_from_rows(rows, start: date, end: date, country: str = "US") -> list[MacroEvent]:
    """Pure: the tracked releases in [start, end], one per day and label
    (FMP lists "CPI" and "Core CPI" as two rows on the same morning)."""
    seen: set[tuple[date, str]] = set()
    events: list[MacroEvent] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("country") != country:
            continue
        label = macro_label(row.get("event", ""))
        if label is None:
            continue
        raw = row.get("date")
        try:
            stamp = datetime.fromisoformat(str(raw)[:19]) if raw else None
        except ValueError:
            stamp = None
        day = stamp.date() if stamp else None
        if day is None or day < start or day > end:
            continue
        key = (day, label)
        if key in seen:
            continue
        seen.add(key)
        # A bare date parses to midnight; only a real clock time is a moment.
        at = stamp.replace(tzinfo=timezone.utc) if stamp and len(str(raw)) > 10 else None
        events.append(MacroEvent(date=day, label=label, event=str(row.get("event", "")), at=at))
    events.sort(key=lambda e: (e.date, e.label))
    return events


async def fetch_macro_events(client: httpx.AsyncClient, api_key: str, start: date, end: date) -> list[MacroEvent] | None:
    """None on a failed call, so the cache can tell "nothing scheduled" from
    "could not ask"."""
    try:
        resp = await client.get(
            f"{_FMP_BASE}/economic-calendar",
            params={"from": start.isoformat(), "to": end.isoformat(), "apikey": api_key},
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        logger.exception("FMP economic calendar fetch failed for %s..%s", start, end)
        return None
    return macro_events_from_rows(rows, start, end)


class MacroCalendar:
    """The coming weeks' tracked releases, cached and single-flighted --
    one request serves every symbol's strip."""

    def __init__(
        self,
        api_key: str,
        *,
        ttl: float = CACHE_TTL_SECONDS,
        window_days: int = WINDOW_DAYS,
        now=time.monotonic,
        today=date.today,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._ttl = ttl
        self._window_days = window_days
        self._now = now
        self._today = today
        self._client = client
        self._events: list[MacroEvent] | None = None
        self._fetched_at = 0.0
        self._on_date: date | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def upcoming(self) -> list[MacroEvent]:
        """Tracked releases from today through the window. Empty when FMP
        is not configured or the call failed -- "not known", not "none"."""
        if not self._api_key:
            return []
        today = self._today()
        async with self._lock:
            fresh = self._events is not None and self._on_date == today and self._now() - self._fetched_at < self._ttl
            if fresh:
                return list(self._events or [])
            end = today + timedelta(days=self._window_days)
            if self._client is not None:
                events = await fetch_macro_events(self._client, self._api_key, today, end)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    events = await fetch_macro_events(client, self._api_key, today, end)
            if events is None:
                # A failed call is not cached: the next asker retries.
                return list(self._events or [])
            self._events = events
            self._fetched_at = self._now()
            self._on_date = today
            return list(events)


# The chart looks back rather than ahead: the minute feed spans a week or
# so, a daily chart two years. Asked in quarters, because FMP caps the span
# of one calendar request and a chunk is also the unit that caches: a past
# quarter's releases never change, so it is fetched once per process.
HISTORY_CHUNK_DAYS = 90
# The quarter still in progress can gain a release time FMP fills in late.
CURRENT_CHUNK_TTL_SECONDS = 60 * 60
# A quarter that failed for any other reason (FMP's per-minute limit, a
# network drop) waits this long before it is asked again. Without it every
# daily chart load re-asked every failed quarter, and the key those calls
# spend is the one fundamentals, news and earnings run on too.
FAILED_CHUNK_RETRY_SECONDS = 15 * 60


class MacroHistory:
    """Tracked releases over any past window, for the chart's release
    markers. Same source, filter and best-effort posture as MacroCalendar;
    a chunk whose call failed is simply missing from the answer (and asked
    again next time), never cached as empty."""

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        *,
        now=time.monotonic,
        today=date.today,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._now = now
        self._today = today
        self._chunks: dict[date, tuple[float, list[MacroEvent]]] = {}
        # Quarters FMP will not serve on this plan (HTTP 402 -- the free
        # tiers reach back about ten months), never asked again; and quarters
        # that failed otherwise, with when.
        self._unavailable: set[date] = set()
        self._failed_at: dict[date, float] = {}
        self._lock = asyncio.Lock()

    def _chunk_starts(self, start: date, end: date) -> list[date]:
        # Chunks are aligned to a fixed epoch so every window reuses them.
        epoch = date(2000, 1, 1)
        first = epoch + timedelta(days=((start - epoch).days // HISTORY_CHUNK_DAYS) * HISTORY_CHUNK_DAYS)
        starts = []
        while first <= end:
            starts.append(first)
            first += timedelta(days=HISTORY_CHUNK_DAYS)
        return starts

    async def between(self, start: date, end: date) -> list[MacroEvent]:
        if not self._api_key or start > end:
            return []
        today = self._today()
        events: list[MacroEvent] = []
        async with self._lock:
            for chunk in self._chunk_starts(start, end):
                chunk_end = chunk + timedelta(days=HISTORY_CHUNK_DAYS - 1)
                cached = self._chunks.get(chunk)
                settled = chunk_end < today
                if cached is not None and (settled or self._now() - cached[0] < CURRENT_CHUNK_TTL_SECONDS):
                    events.extend(cached[1])
                    continue
                if chunk in self._unavailable:
                    continue
                failed_at = self._failed_at.get(chunk)
                if failed_at is not None and self._now() - failed_at < FAILED_CHUNK_RETRY_SECONDS:
                    if cached is not None:
                        events.extend(cached[1])
                    continue
                fetched = await self._fetch(chunk, chunk_end)
                if fetched is None:
                    if cached is not None:
                        events.extend(cached[1])
                    continue
                self._failed_at.pop(chunk, None)
                self._chunks[chunk] = (self._now(), fetched)
                events.extend(fetched)
        return [e for e in events if start <= e.date <= end]

    async def _fetch(self, start: date, end: date) -> list[MacroEvent] | None:
        """fetch_macro_events, but telling "not on this plan" apart from a
        failure worth retrying -- the one answer that will never change."""
        try:
            resp = await self._client.get(
                f"{_FMP_BASE}/economic-calendar",
                params={"from": start.isoformat(), "to": end.isoformat(), "apikey": self._api_key},
            )
        except Exception:
            logger.warning("FMP economic calendar fetch failed for %s..%s", start, end, exc_info=True)
            self._failed_at[start] = self._now()
            return None
        if resp.status_code == 402:
            logger.info("FMP economic calendar %s..%s is outside this plan's history; not asking again", start, end)
            self._unavailable.add(start)
            return None
        try:
            resp.raise_for_status()
            rows = resp.json()
        except Exception:
            logger.warning("FMP economic calendar fetch failed for %s..%s (HTTP %s)", start, end, resp.status_code)
            self._failed_at[start] = self._now()
            return None
        return macro_events_from_rows(rows, start, end)
