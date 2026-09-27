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

Two reports, because the CFTC splits its world in two. Physical
commodities are in the *disaggregated* report, where the speculators are
"managed money" and the other side is the producers and merchants who own
the physical. Financial futures -- the E-mini S&P behind SPY -- are in
*Traders in Financial Futures*, where the speculators are the leveraged
funds and the other side is the asset managers running long-only money.
Same idea, different columns and different words, so a ReportSpec carries
both and everything below is written once.

A word of caution the UI repeats: in the equity index futures the
leveraged funds are usually net *short*, because the position is one leg
of a basis or hedging trade against cash equities, not a bet that the
index falls. Only the move within that contract's own history says
anything.

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

_HEADERS = {"User-Agent": "trading-dashboard"}

# The report lands weekly; half a day of staleness cannot matter, and the
# app restarts often enough that a longer one would rarely be reached.
TTL_SECONDS = 6 * 60 * 60.0
# How far back the percentile looks. Three years is long enough to hold a
# full cycle of a commodity's positioning and short enough that a regime
# from five years ago does not decide today's reading.
HISTORY_YEARS = 3

@dataclass(frozen=True)
class ReportSpec:
    """One CFTC report: where it lives and which two groups it is read
    through. `spec` is the speculative side, `hedge` the other."""

    dataset: str
    spec_long: str
    spec_short: str
    hedge_long: str
    hedge_short: str
    spec_label: str
    hedge_label: str

    @property
    def url(self) -> str:
        return f"https://publicreporting.cftc.gov/resource/{self.dataset}.json"

    @property
    def fields(self) -> str:
        return ",".join(
            ("report_date_as_yyyy_mm_dd", "open_interest_all", self.spec_long, self.spec_short, self.hedge_long, self.hedge_short)
        )


# Physical commodities.
DISAGGREGATED = ReportSpec(
    dataset="72hh-3qpy",
    spec_long="m_money_positions_long_all",
    spec_short="m_money_positions_short_all",
    hedge_long="prod_merc_positions_long",
    hedge_short="prod_merc_positions_short",
    spec_label="managed money",
    hedge_label="producers/merchants",
)

# Financial futures: stock indices, Treasuries, currencies.
FINANCIAL = ReportSpec(
    dataset="gpe5-46if",
    spec_long="lev_money_positions_long",
    spec_short="lev_money_positions_short",
    hedge_long="asset_mgr_positions_long",
    hedge_short="asset_mgr_positions_short",
    spec_label="leveraged funds",
    hedge_label="asset managers",
)


@dataclass(frozen=True)
class Contract:
    code: str
    label: str
    spec: ReportSpec
    # Said on the line itself where the raw net would mislead.
    caveat: str | None = None


_INDEX_CAVEAT = "Leveraged funds are usually net short an index future as the hedge leg of a basis trade; the percentile, not the sign, is the reading."

# ETF -> the contract whose positioning describes it. The ETFs hold or
# track these futures, so the report is about the risk the option on the
# ETF carries.
CONTRACTS: dict[str, Contract] = {
    "USO": Contract("067651", "WTI crude", DISAGGREGATED),
    "UNG": Contract("023651", "Henry Hub natural gas", DISAGGREGATED),
    "GLD": Contract("088691", "Gold", DISAGGREGATED),
    "IAU": Contract("088691", "Gold", DISAGGREGATED),
    "SLV": Contract("084691", "Silver", DISAGGREGATED),
    "SPY": Contract("13874A", "E-mini S&P 500", FINANCIAL, _INDEX_CAVEAT),
    "QQQ": Contract("209742", "E-mini Nasdaq-100", FINANCIAL, _INDEX_CAVEAT),
    "IWM": Contract("239742", "E-mini Russell 2000", FINANCIAL, _INDEX_CAVEAT),
    "TLT": Contract("020601", "UST bond", FINANCIAL),
    "IEF": Contract("043602", "UST 10-year note", FINANCIAL),
}


@dataclass(frozen=True)
class Week:
    """One report date's positioning for one contract."""

    report_date: date
    open_interest: int
    # The speculative side: managed money in a commodity, leveraged funds
    # in a financial future.
    spec_long: int
    spec_short: int
    # The other side: the producers who own the physical, or the asset
    # managers running long-only money.
    hedge_long: int
    hedge_short: int

    @property
    def spec_net(self) -> int:
        return self.spec_long - self.spec_short

    @property
    def hedge_net(self) -> int:
        return self.hedge_long - self.hedge_short

    @property
    def spec_net_pct_oi(self) -> float | None:
        """The net as a share of open interest -- comparable across years
        in a way the raw contract count is not, since open interest itself
        grows and shrinks."""
        if self.open_interest <= 0:
            return None
        return self.spec_net / self.open_interest


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_weeks(rows: list[dict], spec: ReportSpec = DISAGGREGATED) -> list[Week]:
    """Pure: Socrata rows -> weeks, oldest first, read through `spec`'s
    columns. Rows without a usable report date are dropped rather than
    guessed at."""
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
                spec_long=_int(row.get(spec.spec_long)),
                spec_short=_int(row.get(spec.spec_short)),
                hedge_long=_int(row.get(spec.hedge_long)),
                hedge_short=_int(row.get(spec.hedge_short)),
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


def reading(weeks: list[Week], *, contract: Contract, symbol: str) -> dict | None:
    """The line the UI shows: the latest week, its net positioning and
    where that sits in the history. None when nothing usable came back."""
    if not weeks:
        return None
    latest = weeks[-1]
    share = latest.spec_net_pct_oi
    history = [w.spec_net_pct_oi for w in weeks if w.spec_net_pct_oi is not None]
    pct = percentile_of(share, history) if share is not None else None
    return {
        "symbol": symbol,
        "contract": contract.label,
        "spec_label": contract.spec.spec_label,
        "hedge_label": contract.spec.hedge_label,
        "report_date": latest.report_date.isoformat(),
        "open_interest": latest.open_interest,
        "spec_net": latest.spec_net,
        "spec_net_pct_oi": None if share is None else round(share, 4),
        "spec_net_percentile": pct,
        "hedge_net": latest.hedge_net,
        "weeks": len(history),
        # Said plainly, so the card does not have to phrase it: what is
        # unusual, not what to do about it.
        "note": _note(pct, contract),
        "caveat": contract.caveat,
    }


def _note(pct: float | None, contract: Contract) -> str | None:
    if pct is None:
        return None
    # Said as a place in the range, not as long or short: the leveraged
    # funds are net short the bond future at the *top* of their range, and
    # "more net long than in 90 % of weeks" would read as a contradiction
    # of the number right beside it.
    side = contract.spec.spec_label.capitalize()
    if pct >= 0.9:
        return f"{side} sit at the top of their three-year range -- a crowded side, not a sell signal."
    if pct <= 0.1:
        return f"{side} sit at the bottom of their three-year range -- a crowded side, not a buy signal."
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
        lock = self._locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            cached = self._cache.get(symbol)
            if cached is not None and self._now() - cached[0] < TTL_SECONDS:
                return cached[1]
            try:
                rows = await self._fetch(entry)
            except Exception:
                logger.warning("COT fetch failed for %s (%s)", symbol, entry.code, exc_info=True)
                return cached[1] if cached is not None else None
            value = reading(parse_weeks(rows, entry.spec), contract=entry, symbol=symbol)
            self._cache[symbol] = (self._now(), value)
            return value

    async def _fetch(self, contract: Contract) -> list[dict]:
        since = (datetime.now(timezone.utc).date() - timedelta(days=365 * HISTORY_YEARS)).isoformat()
        params = {
            "$select": contract.spec.fields,
            "$where": f"cftc_contract_market_code='{contract.code}' and report_date_as_yyyy_mm_dd>'{since}'",
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": 400,
        }
        resp = await self._client.get(contract.spec.url, params=params, headers=_HEADERS, timeout=20.0)
        resp.raise_for_status()
        return resp.json()
