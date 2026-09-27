"""Commitments of Traders: how the futures behind a commodity ETF are
positioned, from the CFTC's own public feed (Socrata, no key, no cost).

Context, not a signal. The report is published Friday 15:30 ET for
positions held the *Tuesday* before, so it is three days stale on arrival
and weekly thereafter -- useless to the scanner, and of no help to an
entry. What it answers is whether a move is crowded: speculators (managed
money) at the top of their three-year range have little buying left, and
the commercials on the other side of them are the hedgers who own the
physical. That is worth a line beside an options ticket on USO and
nothing more, which is why this module ends at a number and a percentile
and draws no conclusions.

Only the disaggregated report is read (physical commodities). Financial
futures -- the E-mini S&P behind SPY -- live in the Traders in Financial
Futures report, a different dataset with different column names; when a
line for those is wanted it is a second parser, not a new code here.

Best-effort like every outside source in this app: a failed call leaves
the previous answer standing, or returns None ("not known"), which the UI
shows as no line rather than as an error.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

# The disaggregated futures-only report.
_BASE = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
_HEADERS = {"User-Agent": "trading-dashboard"}

# The report lands weekly; half a day of staleness cannot matter, and the
# app restarts often enough that a longer one would rarely be reached.
TTL_SECONDS = 6 * 60 * 60.0
# How far back the percentile looks. Three years is long enough to hold a
# full cycle of a commodity's positioning and short enough that a regime
# from five years ago does not decide today's reading.
HISTORY_YEARS = 3

# ETF -> the contract whose positioning describes it. The ETFs hold (or
# track) these futures directly, so the report is about the same risk the
# option on the ETF carries.
CONTRACTS: dict[str, tuple[str, str]] = {
    "USO": ("067651", "WTI crude"),
    "UNG": ("023651", "Henry Hub natural gas"),
    "GLD": ("088691", "Gold"),
    "IAU": ("088691", "Gold"),
    "SLV": ("084691", "Silver"),
}


@dataclass(frozen=True)
class Week:
    """One report date's positioning for one contract."""

    report_date: date
    open_interest: int
    # Managed money: the trend-following speculators, the side that has to
    # buy to keep a rally going.
    money_long: int
    money_short: int
    # Producers, merchants, processors, users: the physical hedgers, who
    # are usually short into strength.
    commercial_long: int
    commercial_short: int

    @property
    def money_net(self) -> int:
        return self.money_long - self.money_short

    @property
    def commercial_net(self) -> int:
        return self.commercial_long - self.commercial_short

    @property
    def money_net_pct_oi(self) -> float | None:
        """The net as a share of open interest -- comparable across years
        in a way the raw contract count is not, since open interest itself
        grows and shrinks."""
        if self.open_interest <= 0:
            return None
        return self.money_net / self.open_interest


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_weeks(rows: list[dict]) -> list[Week]:
    """Pure: Socrata rows -> weeks, oldest first. Rows without a usable
    report date are dropped rather than guessed at."""
    weeks: list[Week] = []
    for row in rows or []:
        raw = str(row.get("report_date_as_yyyy_mm_dd") or "")
        try:
            report_date = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        weeks.append(
            Week(
                report_date=report_date,
                open_interest=_int(row.get("open_interest_all")),
                money_long=_int(row.get("m_money_positions_long_all")),
                money_short=_int(row.get("m_money_positions_short_all")),
                commercial_long=_int(row.get("prod_merc_positions_long")),
                commercial_short=_int(row.get("prod_merc_positions_short")),
            )
        )
    weeks.sort(key=lambda w: w.report_date)
    return weeks


def percentile_of(value: float, history: list[float]) -> float | None:
    """Where `value` sits in `history`, 0..1: the share strictly below it
    plus half the share equal to it (the mid-rank). Halving the ties is
    what keeps a flat stretch honest -- counting "at or below" puts a
    reading identical to all 52 before it at the 100th percentile, which
    would read as an extreme when nothing has moved at all.

    None when there is too little history to say (under a year of weeks),
    because a percentile of eight readings reads as precision that is not
    there."""
    if len(history) < 52:
        return None
    below = sum(1 for past in history if past < value)
    equal = sum(1 for past in history if past == value)
    return round((below + equal / 2) / len(history), 4)


def reading(weeks: list[Week], *, label: str, symbol: str) -> dict | None:
    """The line the UI shows: the latest week, its net positioning and
    where that sits in the history. None when nothing usable came back."""
    if not weeks:
        return None
    latest = weeks[-1]
    share = latest.money_net_pct_oi
    history = [w.money_net_pct_oi for w in weeks if w.money_net_pct_oi is not None]
    pct = percentile_of(share, history) if share is not None else None
    return {
        "symbol": symbol,
        "contract": label,
        "report_date": latest.report_date.isoformat(),
        "open_interest": latest.open_interest,
        "money_net": latest.money_net,
        "money_net_pct_oi": None if share is None else round(share, 4),
        "money_net_percentile": pct,
        "commercial_net": latest.commercial_net,
        "weeks": len(history),
        # Said plainly, so the card does not have to phrase it: what is
        # unusual, not what to do about it.
        "note": _note(pct),
    }


def _note(pct: float | None) -> str | None:
    if pct is None:
        return None
    if pct >= 0.9:
        return "Speculators are more net long than in 90 % of the past three years -- a crowded side, not a sell signal."
    if pct <= 0.1:
        return "Speculators are more net short than in 90 % of the past three years -- a crowded side, not a buy signal."
    return None


class CotCache:
    """One cached reading per ETF, single-flighted: every options ticket on
    USO asks, and one request a morning should serve them all."""

    def __init__(self, client: httpx.AsyncClient, *, now: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._now = now
        self._cache: dict[str, tuple[float, dict | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def covers(self, symbol: str) -> bool:
        return symbol.upper() in CONTRACTS

    async def reading(self, symbol: str) -> dict | None:
        symbol = symbol.upper()
        entry = CONTRACTS.get(symbol)
        if entry is None:
            return None
        code, label = entry
        lock = self._locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            cached = self._cache.get(symbol)
            if cached is not None and self._now() - cached[0] < TTL_SECONDS:
                return cached[1]
            try:
                rows = await self._fetch(code)
            except Exception:
                logger.warning("COT fetch failed for %s (%s)", symbol, code, exc_info=True)
                return cached[1] if cached is not None else None
            value = reading(parse_weeks(rows), label=label, symbol=symbol)
            self._cache[symbol] = (self._now(), value)
            return value

    async def _fetch(self, code: str) -> list[dict]:
        since = (datetime.now(timezone.utc).date() - timedelta(days=365 * HISTORY_YEARS)).isoformat()
        params = {
            "$select": (
                "report_date_as_yyyy_mm_dd,open_interest_all,m_money_positions_long_all,"
                "m_money_positions_short_all,prod_merc_positions_long,prod_merc_positions_short"
            ),
            "$where": f"cftc_contract_market_code='{code}' and report_date_as_yyyy_mm_dd>'{since}'",
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": 400,
        }
        resp = await self._client.get(_BASE, params=params, headers=_HEADERS, timeout=20.0)
        resp.raise_for_status()
        return resp.json()
