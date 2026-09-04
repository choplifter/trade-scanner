"""When a symbol next reports, and whether an option would be held through it.

The date on its own is trivia. What decides a structure is whether the
event falls *inside* the contract's life: a spread expiring the week before
earnings and one expiring the week after are different trades on the same
chart, and the implied vol priced into each says so. So this module answers
both -- the date, and per candidate expiry whether the report lands before
it.

Rides on the FMP key the app already has (app.market_data.market_conditions
uses the same base and the same best-effort posture). Without FMP_API_KEY
this returns None, which callers must read as "not known", never as "no
earnings coming" -- the whole codebase treats missing data as absent rather
than as a negative signal, and here the difference is the difference
between selling premium into a quiet week and selling it into a print.

Endpoint: FMP's stable Earnings Report API, /stable/earnings?symbol=X --
rows of {symbol, date, epsActual, epsEstimated, revenueActual,
revenueEstimated, lastUpdated} covering reports past and upcoming. The
upcoming ones are deliberately identified by their *date* rather than by a
null epsActual: the estimate fields are unreliable filler on some symbols,
and "the first report dated today or later" needs no such assumption.

Cached for a day. An earnings date does move, but not on the timescale of
a trading session, and this would otherwise be one FMP call per suggestion.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

_FMP_BASE = "https://financialmodelingprep.com/stable"
# An earnings date is stable within a session; re-asking per suggestion
# would spend the FMP quota on an answer that does not change.
CACHE_TTL_SECONDS = 24 * 60 * 60
_TIMEOUT = httpx.Timeout(10.0)


@dataclass(frozen=True)
class EarningsDate:
    symbol: str
    report_date: date
    days_until: int

    def before(self, expiry: date) -> bool:
        """Whether a contract expiring on `expiry` would be held through the
        report. Same-day counts as through it: an expiry on the report date
        is exactly the case where the event decides the outcome."""
        return self.report_date <= expiry

    def to_dict(self) -> dict:
        return {
            "report_date": self.report_date.isoformat(),
            "days_until": self.days_until,
        }


def _parse_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:10]).date()
    except ValueError:
        return None


def next_earnings_from_rows(symbol: str, rows, today: date) -> EarningsDate | None:
    """The soonest report dated today or later. Pure, so the row-shape
    handling is testable without FMP."""
    upcoming = sorted(
        d for d in (_parse_date(row.get("date")) for row in rows or [] if isinstance(row, dict)) if d and d >= today
    )
    if not upcoming:
        return None
    report_date = upcoming[0]
    return EarningsDate(symbol=symbol, report_date=report_date, days_until=(report_date - today).days)


async def fetch_next_earnings(
    client: httpx.AsyncClient, api_key: str, symbol: str, today: date
) -> EarningsDate | None:
    try:
        resp = await client.get(f"{_FMP_BASE}/earnings", params={"symbol": symbol, "apikey": api_key})
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        logger.exception("FMP earnings fetch failed for %s", symbol)
        return None
    return next_earnings_from_rows(symbol, rows, today)


@dataclass
class _Entry:
    value: EarningsDate | None
    fetched_at: float
    on_date: date


class EarningsCalendar:
    """Next-report dates by symbol, cached for a day and single-flighted per
    symbol -- the same shape as the GEX cache, for the same reason: several
    callers asking about one symbol at once should cost one request.

    A symbol with no upcoming report caches that answer too. "FMP knows of
    nothing" is a real result and re-asking every time would spend the quota
    on it."""

    def __init__(
        self,
        api_key: str,
        *,
        ttl: float = CACHE_TTL_SECONDS,
        now=time.monotonic,
        today=date.today,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._ttl = ttl
        self._now = now
        self._today = today
        self._client = client
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, symbol: str) -> asyncio.Lock:
        lock = self._locks.get(symbol)
        if lock is None:
            lock = self._locks[symbol] = asyncio.Lock()
        return lock

    async def next_earnings(self, symbol: str) -> EarningsDate | None:
        """The symbol's next report, or None when there is none upcoming,
        FMP is not configured, or the call failed. All three are the same
        thing to a caller: nothing known, not "nothing coming"."""
        if not self._api_key:
            return None
        symbol = symbol.upper()
        today = self._today()
        async with self._lock(symbol):
            entry = self._entries.get(symbol)
            # days_until is relative to the day it was computed, so an entry
            # from yesterday is wrong even inside the TTL.
            if entry is not None and entry.on_date == today and self._now() - entry.fetched_at < self._ttl:
                return entry.value

            if self._client is not None:
                value = await fetch_next_earnings(self._client, self._api_key, symbol, today)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    value = await fetch_next_earnings(client, self._api_key, symbol, today)
            self._entries[symbol] = _Entry(value=value, fetched_at=self._now(), on_date=today)
            return value
