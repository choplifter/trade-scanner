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
from app.market_data.news import fetch_headlines

logger = logging.getLogger(__name__)

# Alpaca's NewsRequest caps at limit=50 articles per call -- a single call
# across 100+ symbols wouldn't reliably cover every one of them, so stale
# symbols are fetched in smaller batches instead (same batch size proven
# against real coverage rates in the Scanner Match History backfill).
_BATCH_SIZE = 20


class NewsCache:
    def __init__(self, settings: Settings, clients: AlpacaClients):
        self.settings = settings
        self.clients = clients
        self._headlines: dict[str, str | None] = {}
        self._fetched_at: dict[str, float] = {}

    def get(self, symbol: str) -> str | None:
        return self._headlines.get(symbol)

    async def ensure_fresh(self, symbols: Iterable[str]) -> None:
        now = time.monotonic()
        stale = [
            s
            for s in symbols
            if now - self._fetched_at.get(s, -float("inf")) >= self.settings.scanner_news_refresh_interval
        ]
        if not stale:
            return

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
                self._headlines[symbol] = fresh.get(symbol)
