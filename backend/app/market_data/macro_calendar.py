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
from dataclasses import dataclass
from datetime import date, datetime, timedelta

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
            day = datetime.fromisoformat(str(raw)[:19]).date() if raw else None
        except ValueError:
            day = None
        if day is None or day < start or day > end:
            continue
        key = (day, label)
        if key in seen:
            continue
        seen.add(key)
        events.append(MacroEvent(date=day, label=label, event=str(row.get("event", ""))))
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
