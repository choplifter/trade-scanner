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

# FMP's 429 body is a plan-level "Limit Reach" (daily request quota), not a
# per-second burst throttle -- confirmed live: a single isolated request
# with zero concurrency still 429s once the day's quota is spent. Retrying
# soon after a 429 just spends more of a quota that's already at zero, so
# it gets a long backoff. A non-429 failure (network blip, FMP 5xx) is
# genuinely likely transient and safe to retry soon.
RATE_LIMIT_RETRY_SECONDS = 900.0
ERROR_RETRY_SECONDS = 60.0


async def fetch_float_and_market_cap(
    client: httpx.AsyncClient, symbol: str, api_key: str
) -> tuple[float | None, float | None, float | None]:
    """Float shares (FMP /shares-float) and market cap (FMP /profile), plus
    a retry_after_seconds hint for the cache.

    Field names (floatShares, marketCap) are per FMP's published stable-API
    docs/examples as of 2026 -- both endpoints return a one-element array.

    retry_after_seconds is None when both calls either succeeded or came
    back with a clean "no data for this symbol" response -- the normal
    full-length cache TTL is fine, there's nothing to retry sooner for.
    It's set to RATE_LIMIT_RETRY_SECONDS or ERROR_RETRY_SECONDS when a call
    failed, so app.fundamentals.cache retries sooner instead of treating a
    transient or rate-limited failure as if it were settled, cacheable data.
    """
    float_shares: float | None = None
    market_cap: float | None = None
    retry_after: float | None = None

    def _note_failure(exc: Exception) -> None:
        nonlocal retry_after
        this_retry = (
            RATE_LIMIT_RETRY_SECONDS
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            else ERROR_RETRY_SECONDS
        )
        retry_after = this_retry if retry_after is None else min(retry_after, this_retry)

    try:
        resp = await client.get(
            f"{_FMP_BASE}/shares-float", params={"symbol": symbol, "apikey": api_key}
        )
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            float_shares = rows[0].get("floatShares")
    except Exception as exc:
        logger.warning("FMP shares-float fetch failed for %s: %s", symbol, exc)
        _note_failure(exc)

    try:
        resp = await client.get(
            f"{_FMP_BASE}/profile", params={"symbol": symbol, "apikey": api_key}
        )
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            market_cap = rows[0].get("marketCap")
    except Exception as exc:
        logger.warning("FMP profile fetch failed for %s: %s", symbol, exc)
        _note_failure(exc)

    return float_shares, market_cap, retry_after
