"""Thin wrapper around Financial Modeling Prep, which fills in float and
market cap -- fields Alpaca's Assets/Bars/Snapshot APIs don't expose at all
(see app.alpaca.universe for what Alpaca *does* provide). Called at most
once per symbol per app.core.config.Settings.fundamentals_refresh_interval
(see app.fundamentals.cache), not per scan poll, to stay well inside FMP's
free-tier daily request cap. Short interest comes from a separate free
source -- see app.fundamentals.finra_short_interest -- since Finnhub's
short-interest endpoint turned out to require a paid plan.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_FMP_BASE = "https://financialmodelingprep.com/stable"


async def fetch_float_and_market_cap(
    client: httpx.AsyncClient, symbol: str, api_key: str
) -> tuple[float | None, float | None]:
    """Float shares (FMP /shares-float) and market cap (FMP /profile).

    Field names (floatShares, marketCap) are per FMP's published stable-API
    docs/examples as of 2026 -- both endpoints return a one-element array.
    """
    float_shares: float | None = None
    market_cap: float | None = None

    try:
        resp = await client.get(
            f"{_FMP_BASE}/shares-float", params={"symbol": symbol, "apikey": api_key}
        )
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            float_shares = rows[0].get("floatShares")
    except Exception:
        logger.exception("FMP shares-float fetch failed for %s", symbol)

    try:
        resp = await client.get(
            f"{_FMP_BASE}/profile", params={"symbol": symbol, "apikey": api_key}
        )
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            market_cap = rows[0].get("marketCap")
    except Exception:
        logger.exception("FMP profile fetch failed for %s", symbol)

    return float_shares, market_cap
