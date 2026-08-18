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
from app.fundamentals.float_bulk import fetch_all_float
from app.fundamentals.schemas import CompanyProfile, FundamentalsData

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
        self._short_interest_shares: dict[str, float] = {}
        self._short_interest_fetched_at: float = -float("inf")
        # Universe-wide float from FMP's bulk file -- available for every symbol
        # regardless of whether it's currently ranked, unlike self._data which
        # is populated per ranked symbol. See float_bulk.fetch_all_float.
        self._float_shares: dict[str, float] = {}
        self._float_fetched_at: float = -float("inf")

    @property
    def http_client(self):
        """The shared FMP HTTP client. Exposed so callers that need to hit
        another FMP endpoint with the same key and timeout -- the symbol
        panel's news fetch -- can reuse this connection pool instead of
        opening their own or reaching for the private attribute.
        """
        return self._client

    def get(self, symbol: str) -> FundamentalsData | None:
        return self._data.get(symbol)

    def float_shares(self, symbol: str) -> float | None:
        """Float for any symbol, ranked or not.

        This is the piece that makes float usable as a ranking input: it's
        populated for the whole universe from one bulk file, so it's available
        *before* ranking rather than only after a symbol has already surfaced.
        """
        return self._float_shares.get(symbol)

    def short_interest_pct(self, symbol: str) -> float | None:
        """Short interest as a % of float, for any symbol, ranked or not.

        Both inputs are already universe-wide bulk files -- FINRA's short
        interest and FMP's shares-float -- so this needs no per-symbol fetch,
        which is what lets it be screened on across the whole universe rather
        than only on rows that happen to be ranked. self.get(symbol) carries
        the same figure for ranked symbols; this is the version that works
        before a symbol has surfaced.
        """
        shares_short = self._short_interest_shares.get(symbol)
        float_shares = self._float_shares.get(symbol)
        if not shares_short or not float_shares:
            return None
        return shares_short / float_shares * 100

    # Below this share of the consolidated tape, a volume-weighted figure
    # computed from our own feed stops meaning anything. Calibrated on a
    # measured pair rather than picked: on 2026-08-17 we saw 3.04% of AAPL's
    # tape and our session VWAP was 0.10% off the true volume-weighted price,
    # while on IPST we saw 0.24% and were 5.83% off -- 7.81 against 7.38,
    # which put price on the wrong side of VWAP for the whole afternoon. 1%
    # sits between the two, nearer the broken end.
    MIN_TAPE_COVERAGE_PCT = 1.0

    def tape_coverage_pct(self, symbol: str, observed_volume: float | None) -> float | None:
        """What share of today's real volume our feed actually saw, in %.

        None when FMP hasn't reported a volume for this symbol yet, which is
        "unknown", not "fine" -- callers must not read a missing value as good
        coverage.

        The Alpaca IEX feed reports only trades routed through IEX, and that
        share varies enormously by symbol: a few percent on a mega cap, a
        fraction of one percent on a thin one. Any figure weighted by our
        volume inherits that -- VWAP most of all, since it is *defined* as a
        volume weighting. FMP's profile carries consolidated-tape volume in
        the same request that already fetches market cap, so this costs no
        extra call.

        Note both sides move during the session and FMP's figure refreshes on
        its own cadence, so this is an order-of-magnitude check, not a precise
        ratio. That is all it needs to be: the gap between usable and useless
        here is 3% versus 0.24%.
        """
        data = self._data.get(symbol)
        if data is None or not data.full_tape_volume or observed_volume is None:
            return None
        if observed_volume <= 0:
            return 0.0
        return min(observed_volume / data.full_tape_volume * 100, 100.0)

    def is_vwap_reliable(self, symbol: str, observed_volume: float | None) -> bool | None:
        """Whether a VWAP computed from our own feed is worth acting on.

        None means unknown -- see tape_coverage_pct. Kept tri-state on purpose
        so a caller can tell "we checked and it is thin" apart from "we have
        no idea", which are very different things to show a trader.
        """
        coverage = self.tape_coverage_pct(symbol, observed_volume)
        if coverage is None:
            return None
        return coverage >= self.MIN_TAPE_COVERAGE_PCT

    @property
    def float_universe_size(self) -> int:
        return len(self._float_shares)

    async def ensure_fresh(self, symbols: Iterable[str]) -> None:
        """Called both from ScannerEngine's long-lived event loop (every
        poll) and, for a single user-selected symbol, from Dash callbacks
        via async_bridge.run_async -- which spins up a brand new event
        loop per call (asyncio.run()). A Semaphore is bound to whichever
        loop it was created in, so it MUST be created fresh here rather
        than stored as an instance attribute: an instance-level one bound
        at __init__ time to FastAPI's loop raised "bound to a different
        event loop" the first time a Dash callback (a different loop)
        tried to use it. Scoping it to this call's own batch is also
        exactly right for what it's limiting (concurrency within one
        ensure_fresh's worth of symbols), not something that needs to
        persist across calls.
        """
        if not self.settings.has_fmp_credentials:
            return

        await self._ensure_short_interest_file_fresh()
        await self._ensure_float_file_fresh()

        now = time.monotonic()
        stale = [
            s
            for s in symbols
            if now - self._fetched_at.get(s, -float("inf"))
            >= self.settings.fundamentals_refresh_interval
        ]
        if not stale:
            return

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)
        await asyncio.gather(*(self._refresh_one(s, semaphore) for s in stale))

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

    async def _ensure_float_file_fresh(self) -> None:
        now = time.monotonic()
        if now - self._float_fetched_at < self.settings.fundamentals_refresh_interval:
            return
        self._float_fetched_at = now

        try:
            shares = await fetch_all_float(self._client, self.settings.fmp_api_key)
        except Exception:
            logger.exception("Bulk shares-float refresh failed")
            return
        if shares:
            self._float_shares = shares

    async def _refresh_one(self, symbol: str, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            float_shares: float | None = None
            market_cap: float | None = None
            profile: CompanyProfile | None = None
            full_tape_volume: float | None = None
            full_tape_avg_volume: float | None = None
            retry_after: float | None = None
            try:
                (
                    float_shares,
                    market_cap,
                    profile,
                    full_tape_volume,
                    full_tape_avg_volume,
                    retry_after,
                ) = await fetch_float_and_market_cap(
                    self._client, symbol, self.settings.fmp_api_key
                )
            except Exception:
                logger.exception("Fundamentals refresh (FMP) failed for %s", symbol)
                retry_after = ERROR_RETRY_SECONDS

        # Fall back to the bulk file's float when the per-symbol call didn't
        # return one (same FMP source, so no consistency concern) -- that also
        # rescues short_interest_pct, which is unavailable without a float.
        if not float_shares:
            float_shares = self._float_shares.get(symbol)

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
            profile=profile,
            full_tape_volume=full_tape_volume,
            full_tape_avg_volume=full_tape_avg_volume,
            updated_at=datetime.now(timezone.utc),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
