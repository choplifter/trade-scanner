"""Per-symbol fundamentals cache.

Float/market cap/short interest change rarely intraday (short interest is
only reported biweekly by FINRA), so there's no need to poll them on the
scanner's 5-10s cadence. ensure_fresh() is called once per scan poll (see
ScannerEngine.run_loop) with just the symbols in the current ranked views
(a couple dozen, not the whole multi-thousand-symbol universe).

Float/market cap come from FMP, fetched per symbol and only for symbols
that are missing or older than Settings.fundamentals_refresh_interval, to
stay well inside FMP's free-tier daily request cap. Short interest comes
from FINRA's free bulk file (see app.fundamentals.finra_short_interest),
fetched whole (covers every symbol at once) on the same cadence, then
combined with each symbol's float to compute a percentage -- FINRA only
publishes the raw shorted-share count, not a percent-of-float figure.
"""

import asyncio
import logging
import time
from collections.abc import Iterable
from datetime import datetime, timezone

import httpx

from app.core.config import Settings
from app.fundamentals.client import ERROR_RETRY_SECONDS, fetch_float_and_market_cap
from app.fundamentals.finra_short_interest import fetch_latest_short_interest
from app.fundamentals.schemas import FundamentalsData

logger = logging.getLogger(__name__)

# Kept low deliberately: FMP's free tier 429s (see app.fundamentals.client)
# on what turned out to be a *daily* quota, not a per-second one, but firing
# fewer requests at once still avoids tripping any secondary per-second
# throttle FMP's free tier might also have on top of the daily cap.
_MAX_CONCURRENT_FETCHES = 2


class FundamentalsCache:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self._client = client
        self._data: dict[str, FundamentalsData] = {}
        self._fetched_at: dict[str, float] = {}
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)
        self._short_interest_shares: dict[str, float] = {}
        self._short_interest_fetched_at: float = -float("inf")

    def get(self, symbol: str) -> FundamentalsData | None:
        return self._data.get(symbol)

    async def ensure_fresh(self, symbols: Iterable[str]) -> None:
        if not self.settings.has_fmp_credentials:
            return

        await self._ensure_short_interest_file_fresh()

        now = time.monotonic()
        stale = [
            s
            for s in symbols
            if now - self._fetched_at.get(s, -float("inf"))
            >= self.settings.fundamentals_refresh_interval
        ]
        if not stale:
            return

        await asyncio.gather(*(self._refresh_one(s) for s in stale))

    async def _ensure_short_interest_file_fresh(self) -> None:
        now = time.monotonic()
        if now - self._short_interest_fetched_at < self.settings.fundamentals_refresh_interval:
            return
        self._short_interest_fetched_at = now

        try:
            shares = await fetch_latest_short_interest(self._client)
        except Exception:
            logger.exception("FINRA short-interest file refresh failed")
            return
        if shares:
            self._short_interest_shares = shares

    async def _refresh_one(self, symbol: str) -> None:
        async with self._semaphore:
            float_shares: float | None = None
            market_cap: float | None = None
            retry_after: float | None = None
            try:
                float_shares, market_cap, retry_after = await fetch_float_and_market_cap(
                    self._client, symbol, self.settings.fmp_api_key
                )
            except Exception:
                logger.exception("Fundamentals refresh (FMP) failed for %s", symbol)
                retry_after = ERROR_RETRY_SECONDS

        short_interest_pct: float | None = None
        shares_short = self._short_interest_shares.get(symbol)
        if shares_short is not None and float_shares:
            short_interest_pct = shares_short / float_shares * 100

        now = time.monotonic()
        # A clean fetch (retry_after None, even if the fields themselves
        # came back empty) gets the full TTL -- there's nothing to retry
        # sooner for. A failed one is backdated so it goes stale again
        # after retry_after seconds instead of the full interval, without
        # needing a second timestamp dict (see the ensure_fresh staleness
        # check this has to satisfy).
        self._fetched_at[symbol] = (
            now
            if retry_after is None
            else now - self.settings.fundamentals_refresh_interval + retry_after
        )
        self._data[symbol] = FundamentalsData(
            symbol=symbol,
            float_shares=float_shares,
            market_cap=market_cap,
            short_interest_pct=short_interest_pct,
            updated_at=datetime.now(timezone.utc),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
