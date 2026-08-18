"""Per-symbol news-headline cache for the live scanner tables.

Unlike Scanner Match History / the Scanner Benchmark tracker, which fetch a
headline once per symbol *per appearance event* (rare), the live scanner
views show up to 50 rows per view, refreshed every 5-10s, for as long as a
symbol stays ranked -- fetching news on every poll tick would be wasteful
and risks tripping a rate limit. This mirrors app.fundamentals.cache.
FundamentalsCache instead: cache per symbol, refresh on a slow interval,
only ever asked about whatever's currently in a ranked view (see
ScannerEngine._attach_news), not the whole multi-thousand-symbol universe.
"""

import asyncio
import logging
import time
from collections.abc import Iterable

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.market_data.fmp_news import fetch_fmp_headlines
from app.market_data.news import fetch_headlines

logger = logging.getLogger(__name__)

# Alpaca's NewsRequest caps at limit=50 articles per call -- a single call
# across 100+ symbols wouldn't reliably cover every one of them, so stale
# symbols are fetched in smaller batches instead (same batch size proven
# against real coverage rates in the Scanner Match History backfill).
_BATCH_SIZE = 20


ALPACA = "alpaca"
FMP = "fmp"


class NewsCache:
    """Alpaca first, FMP only where Alpaca found nothing.

    Not a swap, and the order matters. Alpaca's Benzinga feed is what the
    catalyst boost was calibrated against (+9.1pp win rate on gainers, see
    formulas._CATALYST_BOOST), so it stays authoritative wherever it has an
    answer. FMP fills the gaps -- measured live, Alpaca covered 2 of 8
    scanner symbols and FMP 7 of 8 -- but carries ~30% litigation spam, so
    everything it returns passes fmp_news.is_low_signal_headline first.

    The source is recorded per symbol so the drift report can measure the two
    feeds separately. That matters: the boost's calibration describes Alpaca
    headlines, and pooling a second feed's catalysts into it would silently
    change what "has_headline" means without changing the multiplier derived
    from it.
    """

    def __init__(self, settings: Settings, clients: AlpacaClients, http_client=None):
        self.settings = settings
        self.clients = clients
        # Only needed for the FMP fallback; None disables it entirely, which
        # is also what happens without an FMP key.
        self.http_client = http_client
        self._headlines: dict[str, str | None] = {}
        # symbol -> monotonic time before which FMP is not worth asking again,
        # because it already said it had nothing. See _FMP_MISS_TTL_SECONDS.
        self._fmp_miss_until: dict[str, float] = {}
        self._sources: dict[str, str | None] = {}
        self._fetched_at: dict[str, float] = {}

    def get(self, symbol: str) -> str | None:
        return self._headlines.get(symbol)

    def source(self, symbol: str) -> str | None:
        """Which feed the cached headline came from, or None if there isn't
        one. Recorded onto each new scanner appearance so catalyst
        performance can be re-checked per feed rather than pooled.
        """
        return self._sources.get(symbol)

    async def ensure_fresh(self, symbols: Iterable[str]) -> None:
        now = time.monotonic()
        stale = [
            s
            for s in symbols
            if now - self._fetched_at.get(s, -float("inf")) >= self.settings.scanner_news_refresh_interval
        ]
        if not stale:
            return

        uncovered: list[str] = []
        for i in range(0, len(stale), _BATCH_SIZE):
            batch = stale[i : i + _BATCH_SIZE]
            try:
                fresh = await fetch_headlines(self.clients, batch)
            except Exception:
                logger.exception("Scanner news refresh failed for a batch of %d symbols", len(batch))
                continue
            for symbol in batch:
                self._fetched_at[symbol] = now
                # Explicitly set (not just for symbols fetch_headlines
                # found) so a headline that's aged out of the 48h lookback
                # window correctly clears instead of sticking forever.
                headline = fresh.get(symbol)
                self._headlines[symbol] = headline
                self._sources[symbol] = ALPACA if headline else None
                if not headline:
                    uncovered.append(symbol)

        await self._fill_gaps_from_fmp(uncovered)

    # How long a "FMP had nothing for this symbol" answer is trusted before
    # asking again. Deliberately longer than scanner_news_refresh_interval:
    # Alpaca is re-checked on that cadence, so a story breaking in the gap is
    # still caught by the primary feed. What this stops is the fallback
    # re-confirming, every fifteen minutes, that a quiet stock is still quiet
    # -- which was the bulk of this app's FMP traffic.
    _FMP_MISS_TTL_SECONDS = 1800.0

    async def _fill_gaps_from_fmp(self, symbols: list[str]) -> None:
        """Second pass, for symbols Alpaca had nothing for.

        Only the gaps, never an override: where both feeds have a story,
        Alpaca's wins, because that's the feed the catalyst boost's numbers
        describe. Silent no-op without an HTTP client or an FMP key, same as
        every other FMP-dependent feature here.
        """
        if not symbols or self.http_client is None or not self.settings.has_fmp_credentials:
            return

        now = time.monotonic()
        askable = [s for s in symbols if now >= self._fmp_miss_until.get(s, 0.0)]
        if not askable:
            return

        try:
            fallback = await fetch_fmp_headlines(
                self.http_client, self.settings.fmp_api_key, askable
            )
        except Exception:
            logger.exception("FMP news fallback failed for %d symbols", len(askable))
            return

        # Remember the misses. A symbol FMP answered for is cleared, so a
        # story that arrives later is not suppressed by a stale miss.
        for symbol in askable:
            if symbol in fallback:
                self._fmp_miss_until.pop(symbol, None)
            else:
                self._fmp_miss_until[symbol] = now + self._FMP_MISS_TTL_SECONDS

        for symbol, headline in fallback.items():
            self._headlines[symbol] = headline
            self._sources[symbol] = FMP
        if fallback:
            logger.info(
                "FMP news fallback filled %d of %d symbols Alpaca had no headline for "
                "(%d skipped as recent misses)",
                len(fallback),
                len(askable),
                len(symbols) - len(askable),
            )
