"""At-a-glance red/yellow/green market-conditions assessment -- VIX level,
today's high-impact global economic events, and scanner breadth, combined
by "worst signal wins" into one indicator. Deliberately descriptive, not
advisory: this reports what conditions look like, not whether to trade
(same non-directive framing as app.ai.trade_ideas' system prompt).

Both external signals ride on the already-configured FMP_API_KEY (same
provider used for float/market cap/short interest -- see
app.fundamentals.client) rather than a new credential:
- VIX: FMP's /quote endpoint on the ^VIX symbol -- the real CBOE index
  value. Alpaca has no index-data endpoint at all (confirmed: no indices
  client in alpaca-py, and ^VIX 400s against the stock snapshot endpoint),
  so an ETF proxy (VIXY/VXX) was the only Alpaca-side alternative; FMP's
  real index quote is strictly better.
- High-impact events: FMP's /economic-calendar, filtered client-side to
  a handful of major economies and impact="High" (the endpoint has no
  server-side country filter).
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_FMP_BASE = "https://financialmodelingprep.com/stable"
# Cboe publishes its own index, delayed, without a key or an account --
# the shortest path between the number and this app, and one provider
# less to depend on for it. FMP stays as the fallback: it is fetched for
# the events and the breadth in the same pass anyway.
_CBOE_VIX = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json"
# ^TNX, Cboe's 10-year Treasury yield index: the yield times ten (49.98 is
# 4.998 %). Shown beside the VIX, not scored -- rates are the biggest single
# input to index valuations, but there is no agreed "too high" the way VIX
# 25 is agreed, so it informs the reader rather than the colour.
_CBOE_TNX = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_TNX.json"

# "Global" here means the economies whose scheduled releases are actually
# likely to move a US small/mid-cap scanner universe -- not literally every
# country FMP's calendar covers (which includes dozens of minor markets
# irrelevant to this app's universe).
_TRACKED_COUNTRIES = {"US", "EU", "GB", "CN", "JP"}

# VIX bands are the standard, widely-used interpretation (not a bespoke
# threshold): sub-20 is calm, 20-25 is elevated caution, 25+ is fear.
# change_pct thresholds catch a sharp *spike* as its own signal, independent
# of the absolute level -- a fast move from 14 to 17 (+21%) is a real stress
# signal even though 17 alone would still read as calm.
_VIX_RED = 25.0
_VIX_YELLOW = 20.0
_VIX_CHANGE_RED = 10.0
_VIX_CHANGE_YELLOW = 5.0

# Breadth is deliberately one-sided: a broad rally (high % green) isn't a
# risk signal worth flagging, only a broad selloff is.
_BREADTH_RED = 25.0
_BREADTH_YELLOW = 40.0


@dataclass
class VixReading:
    price: float
    change_pct: float


@dataclass
class TenYearReading:
    yield_pct: float
    change_bp: float


@dataclass
class EconomicEvent:
    date: datetime
    country: str
    event: str
    impact: str


@dataclass
class MarketConditions:
    level: str  # "green" | "yellow" | "red"
    reasons: list[str] = field(default_factory=list)
    vix: VixReading | None = None
    ten_year: TenYearReading | None = None
    high_impact_events_today: list[EconomicEvent] = field(default_factory=list)
    breadth_pct: float | None = None


async def fetch_vix_cboe(client: httpx.AsyncClient) -> VixReading | None:
    """The VIX from the exchange that computes it. Delayed by a quarter of
    an hour, which a regime reading does not care about."""
    try:
        resp = await client.get(_CBOE_VIX, headers={"User-Agent": "trading-dashboard"})
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
    except Exception:
        logger.warning("Cboe VIX quote fetch failed", exc_info=True)
        return None
    price = data.get("current_price") or data.get("close")
    change_pct = data.get("price_change_percent")
    if price is None or change_pct is None:
        return None
    return VixReading(price=float(price), change_pct=float(change_pct))


async def fetch_vix_fmp(client: httpx.AsyncClient, api_key: str) -> VixReading | None:
    try:
        resp = await client.get(f"{_FMP_BASE}/quote", params={"symbol": "^VIX", "apikey": api_key})
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        logger.exception("FMP VIX quote fetch failed")
        return None
    if not rows:
        return None
    row = rows[0]
    price = row.get("price")
    change_pct = row.get("changePercentage")
    if price is None or change_pct is None:
        return None
    return VixReading(price=price, change_pct=change_pct)


async def fetch_vix(client: httpx.AsyncClient, api_key: str) -> VixReading | None:
    """Cboe first, FMP if that is unreachable. Either way it is the same
    index; the fallback exists so one provider going quiet does not take
    the market-conditions readout with it."""
    reading = await fetch_vix_cboe(client)
    if reading is not None:
        return reading
    if not api_key:
        return None
    return await fetch_vix_fmp(client, api_key)


async def fetch_ten_year_cboe(client: httpx.AsyncClient) -> TenYearReading | None:
    try:
        resp = await client.get(_CBOE_TNX, headers={"User-Agent": "trading-dashboard"})
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
    except Exception:
        logger.warning("Cboe 10-year yield fetch failed", exc_info=True)
        return None
    price = data.get("current_price") or data.get("close")
    change = data.get("price_change")
    if price is None or change is None:
        return None
    # TNX points are tenths of a percent, so one point is ten basis points.
    return TenYearReading(yield_pct=float(price) / 10, change_bp=float(change) * 10)


async def fetch_ten_year_fmp(client: httpx.AsyncClient, api_key: str) -> TenYearReading | None:
    """End-of-day only, newest first -- the change is against the day
    before, which is what Cboe's change means too."""
    try:
        resp = await client.get(f"{_FMP_BASE}/treasury-rates", params={"apikey": api_key})
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        logger.exception("FMP treasury rates fetch failed")
        return None
    if not rows or rows[0].get("year10") is None:
        return None
    latest = float(rows[0]["year10"])
    previous = rows[1].get("year10") if len(rows) > 1 else None
    change_bp = (latest - float(previous)) * 100 if previous is not None else 0.0
    return TenYearReading(yield_pct=latest, change_bp=change_bp)


async def fetch_ten_year(client: httpx.AsyncClient, api_key: str) -> TenYearReading | None:
    """Same arrangement as fetch_vix: Cboe first, FMP behind it."""
    reading = await fetch_ten_year_cboe(client)
    if reading is not None:
        return reading
    if not api_key:
        return None
    return await fetch_ten_year_fmp(client, api_key)


async def fetch_high_impact_events_today(
    client: httpx.AsyncClient, api_key: str, today: date, countries: set[str] = _TRACKED_COUNTRIES
) -> list[EconomicEvent]:
    try:
        resp = await client.get(
            f"{_FMP_BASE}/economic-calendar",
            params={"from": today.isoformat(), "to": today.isoformat(), "apikey": api_key},
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        logger.exception("FMP economic calendar fetch failed")
        return []

    events: list[EconomicEvent] = []
    for row in rows:
        if row.get("impact") != "High" or row.get("country") not in countries:
            continue
        raw_date = row.get("date")
        if not raw_date:
            continue
        try:
            event_date = datetime.fromisoformat(raw_date).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        events.append(
            EconomicEvent(
                date=event_date,
                country=row["country"],
                event=row.get("event", ""),
                impact="High",
            )
        )
    events.sort(key=lambda e: e.date)
    return events


def compute_market_conditions(
    vix: VixReading | None,
    events: list[EconomicEvent],
    breadth_pct: float | None,
    ten_year: TenYearReading | None = None,
) -> MarketConditions:
    level = "green"
    reasons: list[str] = []

    def _bump(new_level: str) -> None:
        nonlocal level
        if new_level == "red" or (new_level == "yellow" and level == "green"):
            level = new_level

    if vix is not None:
        if vix.price >= _VIX_RED or vix.change_pct >= _VIX_CHANGE_RED:
            _bump("red")
            reasons.append(f"VIX {vix.price:.1f} ({vix.change_pct:+.1f}% today) -- elevated fear")
        elif vix.price >= _VIX_YELLOW or vix.change_pct >= _VIX_CHANGE_YELLOW:
            _bump("yellow")
            reasons.append(f"VIX {vix.price:.1f} ({vix.change_pct:+.1f}% today) -- caution")

    if events:
        _bump("yellow")
        names = ", ".join(f"{e.country} {e.event}" for e in events[:3])
        suffix = f" (+{len(events) - 3} more)" if len(events) > 3 else ""
        reasons.append(f"High-impact event(s) today: {names}{suffix}")

    if breadth_pct is not None:
        if breadth_pct < _BREADTH_RED:
            _bump("red")
            reasons.append(f"Scanner breadth {breadth_pct:.0f}% green -- broad selloff")
        elif breadth_pct < _BREADTH_YELLOW:
            _bump("yellow")
            reasons.append(f"Scanner breadth {breadth_pct:.0f}% green -- mixed/weak")

    if not reasons:
        reasons.append("VIX calm, no high-impact events today, broad participation")

    return MarketConditions(
        level=level,
        reasons=reasons,
        vix=vix,
        ten_year=ten_year,
        high_impact_events_today=events,
        breadth_pct=breadth_pct,
    )
