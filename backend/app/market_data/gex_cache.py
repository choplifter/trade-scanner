"""GEX for whatever symbol is on screen, not just a fixed list of five.

app.market_data.gamma_exposure computes one symbol's gamma profile; until
now the only caller was the scanner engine's slow loop, which walked a
hardcoded SYMBOLS tuple and left every other ticker without a reading. A
longer list is not the fix: fetch_gex paginates the contract listing *and*
pulls the option chain snapshots for one symbol, so it does not scale to a
whole scanner universe every five minutes.

So the loop stops being the only way in. This cache computes a symbol on
first request and keeps it for as long as the loop's own cadence would
have (settings.gex_refresh_interval), which means an on-demand reading is
never staler than the precomputed ones already were. The engine keeps
warming the handful of symbols that sit on the chart most often; everything
else arrives when somebody actually looks at it.

Same shape as app.options.chain_fetch.ChainCache -- TTL plus one asyncio
Lock per symbol -- for the same reason: several widgets asking about one
symbol at once is the normal case, and it should cost one fetch, not four.

Two things this deliberately does *not* do:

- It does not merge with ChainCache. That cache fetches 60 days out and
  +/-10% of spot, per expiry; GEX wants 45 days and +/-15%, every expiry at
  once. Forcing one window on both would change numbers the rest of the
  options stack depends on to save a fetch that already caches.
- It does not decide whether a reading is "good enough" to show. A thin
  chain produces a real but weakly-supported number, and the honest answer
  is to report what it rests on (contracts_used, open_interest_used) and
  let the reader judge -- not to invent a minimum-liquidity threshold this
  module cannot defend, which is exactly the line gex_plan.py draws for
  itself.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.alpaca.client import AlpacaClients
from app.market_data.gamma_exposure import GexReading, fetch_gex

logger = logging.getLogger(__name__)

# How long a symbol that produced nothing (no spot, no options listed, a
# failed call) is left alone before it is tried again. Without this, a
# ticker with no chain at all would re-fetch on every request -- an easy
# way to spend the API budget on an answer that will not change.
FAILURE_BACKOFF_SECONDS = 60.0
# Enough for a long session of clicking through the scanner without the
# cache growing without bound. Evicted least-recently-used.
MAX_SYMBOLS = 200

Fetcher = Callable[[AlpacaClients, str], Awaitable[GexReading | None]]


@dataclass
class _Entry:
    reading: GexReading
    fetched_at: float


class GexCache:
    """Readings by symbol, computed on demand and shared between the
    engine's warm loop and the API."""

    def __init__(
        self,
        clients: AlpacaClients,
        *,
        ttl: float,
        max_symbols: int = MAX_SYMBOLS,
        failure_backoff: float = FAILURE_BACKOFF_SECONDS,
        fetcher: Fetcher = fetch_gex,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clients = clients
        self._ttl = ttl
        self._max_symbols = max_symbols
        self._failure_backoff = failure_backoff
        self._fetch = fetcher
        self._now = now
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._failed_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, symbol: str) -> asyncio.Lock:
        lock = self._locks.get(symbol)
        if lock is None:
            lock = self._locks[symbol] = asyncio.Lock()
        return lock

    def _fresh(self, symbol: str) -> GexReading | None:
        entry = self._entries.get(symbol)
        if entry is None or self._now() - entry.fetched_at >= self._ttl:
            return None
        self._entries.move_to_end(symbol)
        return entry.reading

    def peek(self, symbol: str) -> GexReading | None:
        """Whatever is cached for this symbol, fresh or not, without
        fetching. For callers that must not block on a cold symbol."""
        entry = self._entries.get(symbol.upper())
        return entry.reading if entry is not None else None

    def cached(self) -> dict[str, GexReading]:
        """Every reading held right now, stale ones included -- each
        carries its own as_of, and a five-minute-old gamma profile is still
        the answer to "where are the walls"."""
        return {symbol: entry.reading for symbol, entry in self._entries.items()}

    async def reading(self, symbol: str) -> GexReading | None:
        """This symbol's reading, computing it if the cache has nothing
        fresh. One fetch per symbol at a time however many callers ask."""
        symbol = symbol.upper()
        fresh = self._fresh(symbol)
        if fresh is not None:
            return fresh
        async with self._lock(symbol):
            # Another caller may have filled it while this one waited.
            fresh = self._fresh(symbol)
            if fresh is not None:
                return fresh
            return await self._fetch_locked(symbol)

    async def refresh(self, symbol: str) -> GexReading | None:
        """Recompute regardless of the TTL -- the engine's warm loop, which
        has its own cadence."""
        symbol = symbol.upper()
        async with self._lock(symbol):
            return await self._fetch_locked(symbol, force=True)

    async def _fetch_locked(self, symbol: str, *, force: bool = False) -> GexReading | None:
        failed_at = self._failed_at.get(symbol)
        if not force and failed_at is not None and self._now() - failed_at < self._failure_backoff:
            entry = self._entries.get(symbol)
            return entry.reading if entry is not None else None

        # fetch_gex is best-effort by contract: it logs and returns None
        # rather than raising, so there is nothing to catch here.
        reading = await self._fetch(self._clients, symbol)
        if reading is None:
            self._failed_at[symbol] = self._now()
            entry = self._entries.get(symbol)
            # A failed fetch is not an answer. Whatever was cached before
            # is still the best available one.
            return entry.reading if entry is not None else None

        self._failed_at.pop(symbol, None)
        self._entries[symbol] = _Entry(reading, self._now())
        self._entries.move_to_end(symbol)
        self._evict()
        return reading

    def _evict(self) -> None:
        while len(self._entries) > self._max_symbols:
            symbol, _ = self._entries.popitem(last=False)
            self._locks.pop(symbol, None)
            self._failed_at.pop(symbol, None)

