"""Who reports earnings today and next session, and what their options
say about it -- the Earnings screen's data.

app.market_data.earnings asks FMP one symbol at a time ("when does AEO
report next?"), which is the right question for a chain the reader has
already opened. The screen asks the opposite one: "who reports on this
date?" -- FMP's /earnings-calendar by date, one call for the whole day,
cached for half an hour because `epsActual` flips from null to a number as
companies publish, and the screen shows that as "already reported".

Two lists come out of this module:

- the **screen** (build_earnings_screen): the calendar's symbols
  intersected with what Alpaca actually trades, filtered by trailing
  dollar volume from one batched daily-bars call, sorted most liquid
  first. Cheap enough to answer in the time a page takes to render.
- the **facts** (gather_symbol_facts): per symbol, the front expiry's
  at-the-money implied volatility as an implied move, the strike count
  near the money as a liquidity hint, and the stock's moves over its past
  reports (app.options.events). One expiries call and one chain per
  symbol, so it is a second request the widget fills in after the list.

Same posture as every other FMP-backed feature here: without a key or on
a failed call the calendar answers an empty list, which callers read as
"not known", never as "nobody reports today". Every per-symbol fact is
gathered inside its own try/except and a failure is a None field, never a
dropped row -- a symbol whose chain could not be read still reports.
"""

from __future__ import annotations

import asyncio
import logging
import math
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import httpx

from app.options.events import BARS_LOOKBACK_DAYS, closes_by_day, earnings_moves, summarize_moves
from app.options.iv_context import atm_iv

logger = logging.getLogger(__name__)

_FMP_BASE = "https://financialmodelingprep.com/stable"
_TIMEOUT = httpx.Timeout(10.0)

# The calendar changes during the day only in one way -- epsActual gets
# filled in as companies publish -- so half an hour keeps "already
# reported" honest without spending the quota.
CACHE_TTL_SECONDS = 30 * 60
# Sessions of daily bars behind the dollar-volume figure; calendar days
# fetched to cover them with slack for holidays.
LIQUIDITY_SESSIONS = 20
LIQUIDITY_LOOKBACK_DAYS = 35
# How long a symbol's facts stand before the chain is read again. The
# implied move to a two-day expiry does not change materially in five
# minutes, and a widget refresh should not cost thirty chain reads.
FACTS_TTL_SECONDS = 300.0
FACTS_CONCURRENCY = 6

Session = Literal["today", "next"]


# --- the calendar -----------------------------------------------------------------


@dataclass(frozen=True)
class Reporter:
    symbol: str
    report_date: date
    # FMP fills epsActual once the company has published. Null before.
    reported: bool
    eps_estimated: float | None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "report_date": self.report_date.isoformat(),
            "reported": self.reported,
            "eps_estimated": self.eps_estimated,
        }


def _parse_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:10]).date()
    except ValueError:
        return None


def _num(raw) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def reporters_from_rows(rows, start: date, end: date) -> list[Reporter]:
    """Pure: the calendar rows dated within [start, end], one per symbol
    and date, sorted by date then symbol. Rows without a usable symbol or
    date are skipped rather than guessed at."""
    seen: set[tuple[str, date]] = set()
    out: list[Reporter] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        day = _parse_date(row.get("date"))
        if not symbol or day is None or day < start or day > end:
            continue
        key = (symbol, day)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Reporter(
                symbol=symbol,
                report_date=day,
                reported=_num(row.get("epsActual")) is not None,
                eps_estimated=_num(row.get("epsEstimated")),
            )
        )
    out.sort(key=lambda r: (r.report_date, r.symbol))
    return out


async def fetch_earnings_calendar(client: httpx.AsyncClient, api_key: str, start: date, end: date) -> list[Reporter] | None:
    """None on a failed call, so the cache can tell "nobody listed" from
    "could not ask"."""
    try:
        resp = await client.get(
            f"{_FMP_BASE}/earnings-calendar",
            params={"from": start.isoformat(), "to": end.isoformat(), "apikey": api_key},
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        logger.exception("FMP earnings calendar fetch failed for %s..%s", start, end)
        return None
    return reporters_from_rows(rows, start, end)


class EarningsDayCalendar:
    """The reporters in a date window, cached per window and single-
    flighted -- one request serves every open widget."""

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
        self._entries: dict[tuple[date, date], tuple[float, date, list[Reporter]]] = {}
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def reporters(self, start: date, end: date) -> list[Reporter]:
        """Everyone FMP lists in [start, end]. Empty when FMP is not
        configured or the call failed -- "not known", not "nobody"."""
        if not self._api_key:
            return []
        today = self._today()
        key = (start, end)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                fetched_at, on_date, cached = entry
                if on_date == today and self._now() - fetched_at < self._ttl:
                    return list(cached)
            if self._client is not None:
                found = await fetch_earnings_calendar(self._client, self._api_key, start, end)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    found = await fetch_earnings_calendar(client, self._api_key, start, end)
            if found is None:
                # A failed call is not cached: the next asker retries, and
                # meanwhile the last good answer (if any) stands.
                return list(entry[2]) if entry is not None else []
            self._entries[key] = (self._now(), today, found)
            return list(found)


# --- the screen -------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenRow:
    symbol: str
    report_date: date
    session: Session
    reported: bool
    company_name: str | None
    last: float | None
    avg_dollar_vol_20d: float
    dollar_volume_today: float | None
    pct_change: float | None
    market_cap: float | None
    sector: str | None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "report_date": self.report_date.isoformat(),
            "session": self.session,
            "reported": self.reported,
            "company_name": self.company_name,
            "last": None if self.last is None else round(self.last, 4),
            "avg_dollar_vol_20d": round(self.avg_dollar_vol_20d, 2),
            "dollar_volume_today": None if self.dollar_volume_today is None else round(self.dollar_volume_today, 2),
            "pct_change": None if self.pct_change is None else round(self.pct_change, 2),
            "market_cap": self.market_cap,
            "sector": self.sector,
        }


def liquidity_from_bars(bars_by_symbol: dict, *, today: date, sessions: int = LIQUIDITY_SESSIONS) -> dict[str, tuple[float, float]]:
    """Pure: per symbol (last completed close, mean dollar volume over the
    last `sessions` completed bars). Today's bar is still forming and is
    left out, as app.ai.options_context._daily_block does."""
    out: dict[str, tuple[float, float]] = {}
    for symbol, bars in (bars_by_symbol or {}).items():
        completed = []
        for bar in bars or []:
            ts = getattr(bar, "timestamp", None)
            close = getattr(bar, "close", None)
            volume = getattr(bar, "volume", None)
            if ts is None or close is None or volume is None:
                continue
            day = ts.date() if isinstance(ts, datetime) else ts
            if day >= today:
                continue
            completed.append((float(close), float(volume)))
        if not completed:
            continue
        recent = completed[-sessions:]
        out[str(symbol).upper()] = (recent[-1][0], statistics.fmean(c * v for c, v in recent))
    return out


def build_earnings_screen(
    reporters: list[Reporter],
    tradable: dict[str, str | None],
    liquidity: dict[str, tuple[float, float]],
    engine_rows: dict | None,
    *,
    today: date,
    next_session: date,
    min_dollar_volume: float,
) -> list[ScreenRow]:
    """Pure: the reporters Alpaca trades, liquid enough, most liquid first.

    `tradable` maps symbol to company name (None when the listing carries
    none); `liquidity` is liquidity_from_bars' answer; `engine_rows` the
    scanner's live rows, which carry today's numbers when the market is
    open and are simply absent otherwise."""
    rows: list[ScreenRow] = []
    engine_rows = engine_rows or {}
    for reporter in reporters:
        if reporter.report_date == today:
            session: Session = "today"
        elif reporter.report_date == next_session:
            session = "next"
        else:
            continue
        if reporter.symbol not in tradable:
            continue
        liq = liquidity.get(reporter.symbol)
        if liq is None:
            continue
        prev_close, avg_dollar_vol = liq
        if avg_dollar_vol < min_dollar_volume:
            continue
        live = engine_rows.get(reporter.symbol)
        rows.append(
            ScreenRow(
                symbol=reporter.symbol,
                report_date=reporter.report_date,
                session=session,
                reported=reporter.reported,
                company_name=(getattr(live, "company_name", None) if live is not None else None) or tradable.get(reporter.symbol),
                last=getattr(live, "last_price", None) if live is not None else prev_close,
                avg_dollar_vol_20d=avg_dollar_vol,
                dollar_volume_today=getattr(live, "dollar_volume_today", None) if live is not None else None,
                pct_change=getattr(live, "pct_change", None) if live is not None else None,
                market_cap=getattr(live, "market_cap", None) if live is not None else None,
                sector=getattr(live, "sector", None) if live is not None else None,
            )
        )
    rows.sort(key=lambda r: (-r.avg_dollar_vol_20d, r.symbol))
    return rows


# --- per-symbol facts ---------------------------------------------------------------


@dataclass(frozen=True)
class SymbolFacts:
    symbol: str
    front_expiry: date | None
    front_dte: int | None
    # Strikes listed near the money on the front expiry -- a coarse
    # liquidity hint (two is a dead chain, forty a busy one).
    contract_count: int | None
    atm_iv: float | None
    # One sigma to the front expiry, as a percent of spot: atm_iv * sqrt(T).
    # The same convention the Optimizer and the expiry strip use. On the
    # report day itself there is no pre-report expiry to net out, so this
    # includes the ordinary sessions to expiry, not the report alone.
    implied_move_pct: float | None
    hist_median_pct: float | None
    hist_mean_pct: float | None
    hist_max_pct: float | None
    samples: int
    history_note: str | None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "front_expiry": None if self.front_expiry is None else self.front_expiry.isoformat(),
            "front_dte": self.front_dte,
            "contract_count": self.contract_count,
            "atm_iv": None if self.atm_iv is None else round(self.atm_iv, 4),
            "implied_move_pct": None if self.implied_move_pct is None else round(self.implied_move_pct, 2),
            "hist_median_pct": self.hist_median_pct,
            "hist_mean_pct": self.hist_mean_pct,
            "hist_max_pct": self.hist_max_pct,
            "samples": self.samples,
            "history_note": self.history_note,
        }


def implied_move_pct(iv: float | None, dte: int | None) -> float | None:
    if iv is None or dte is None or dte <= 0 or iv <= 0:
        return None
    return iv * math.sqrt(dte / 365.0) * 100.0


_facts_cache: dict[tuple[str, date], tuple[float, SymbolFacts]] = {}


async def _front_expiry_facts(service, symbol: str) -> tuple[date | None, int | None, int | None, float | None]:
    """(expiry, dte, contract_count, atm_iv) of the first expiry with a
    day left. Any failure is None across the board, logged."""
    try:
        raw = (await service.expiries(symbol)).get("expiries", [])
    except Exception:
        logger.exception("Earnings facts: expiries failed for %s", symbol)
        return None, None, None, None
    front = None
    for e in raw:
        dte = e.dte if hasattr(e, "dte") else e["dte"]
        count = e.contract_count if hasattr(e, "contract_count") else e["contract_count"]
        if dte >= 1 and count > 0:
            expiry = e.expiry if hasattr(e, "expiry") else date.fromisoformat(e["expiry"])
            front = (expiry, dte, count)
            break
    if front is None:
        return None, None, None, None
    expiry, dte, count = front
    try:
        chain = await service.chain(symbol, expiry)
    except Exception:
        logger.exception("Earnings facts: chain failed for %s %s", symbol, expiry)
        return expiry, dte, count, None
    return expiry, dte, count, atm_iv(chain)


async def gather_symbol_facts(
    symbols: list[str],
    *,
    service,
    clients,
    earnings_calendar,
    today: date,
    concurrency: int = FACTS_CONCURRENCY,
    ttl: float = FACTS_TTL_SECONDS,
    now=time.monotonic,
) -> dict[str, SymbolFacts]:
    """The facts per symbol, from one batched bars call plus one expiries
    and one chain read each, at most `concurrency` symbols at a time.
    Cached per symbol and day for `ttl` seconds."""
    wanted = [s.upper() for s in symbols]
    out: dict[str, SymbolFacts] = {}
    pending: list[str] = []
    for symbol in wanted:
        entry = _facts_cache.get((symbol, today))
        if entry is not None and now() - entry[0] < ttl:
            out[symbol] = entry[1]
        else:
            pending.append(symbol)
    if not pending:
        return out

    bars_by_symbol: dict = {}
    bars_note: str | None = None
    if clients is None:
        bars_note = "no market data client for the daily bars"
    else:
        try:
            from app.market_data.bars import get_daily_bars_multi

            bars_by_symbol = await get_daily_bars_multi(clients, pending, lookback_days=BARS_LOOKBACK_DAYS)
        except Exception:
            logger.exception("Earnings facts: daily bars failed for %d symbols", len(pending))
            bars_note = "daily bars unavailable"

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(symbol: str) -> SymbolFacts:
        async with semaphore:
            expiry, dte, count, iv = await _front_expiry_facts(service, symbol)
            summary = summarize_moves([])
            note = bars_note
            if earnings_calendar is None:
                note = note or "no earnings calendar configured"
            else:
                try:
                    dates = await earnings_calendar.report_dates(symbol)
                except Exception:
                    logger.exception("Earnings facts: report dates failed for %s", symbol)
                    dates = []
                past = [d for d in dates if d < today]
                if not past:
                    note = note or "no past report dates known"
                elif bars_note is None:
                    moves = earnings_moves(past, closes_by_day(bars_by_symbol.get(symbol, [])), today=today)
                    summary = summarize_moves(moves)
                    if not moves:
                        note = "no daily closes around the past reports"
            return SymbolFacts(
                symbol=symbol,
                front_expiry=expiry,
                front_dte=dte,
                contract_count=count,
                atm_iv=iv,
                implied_move_pct=implied_move_pct(iv, dte),
                hist_median_pct=summary["median_abs_pct"],
                hist_mean_pct=summary["mean_abs_pct"],
                hist_max_pct=summary["max_abs_pct"],
                samples=summary["samples"],
                history_note=note,
            )

    for facts in await asyncio.gather(*(one(symbol) for symbol in pending)):
        _facts_cache[(facts.symbol, today)] = (now(), facts)
        out[facts.symbol] = facts
    return out


def clear_facts_cache() -> None:
    _facts_cache.clear()


__all__ = [
    "CACHE_TTL_SECONDS",
    "EarningsDayCalendar",
    "Reporter",
    "ScreenRow",
    "SymbolFacts",
    "build_earnings_screen",
    "clear_facts_cache",
    "fetch_earnings_calendar",
    "gather_symbol_facts",
    "implied_move_pct",
    "liquidity_from_bars",
    "reporters_from_rows",
]
