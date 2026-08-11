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
    high_impact_events_today: list[EconomicEvent] = field(default_factory=list)
    breadth_pct: float | None = None


async def fetch_vix(client: httpx.AsyncClient, api_key: str) -> VixReading | None:
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
    vix: VixReading | None, events: list[EconomicEvent], breadth_pct: float | None
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
        high_impact_events_today=events,
        breadth_pct=breadth_pct,
    )
