"""In-memory tracker for a live cross-symbol news feed -- backs the
NewsFeedWidget's "wire" of recently-published articles across whatever
symbols are currently ranked in a fixed scanner view.

Not persisted -- same "personal-dashboard live feed, not a compliance
record" rationale as app.scanners.benchmark_tracker.ScannerBenchmarkTracker.
A server restart just means the feed starts over; new articles for
still-ranked symbols get picked back up on the next poll.

Deliberately not built on app.market_data.news.fetch_headlines: that
function collapses to one headline per symbol (the most recent), which is
exactly the information this needs to *not* collapse -- every new article,
for every symbol it's tagged to, is its own feed entry (one row per
(article, symbol) pair, matching how ScannerRow.recent_headline already
treats news per-symbol without cross-symbol dedup). A separate, simple
fetch here also means this never risks touching that function's
already-working roundup-classification logic.
"""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone

from alpaca.common.enums import Sort
from alpaca.data.requests import NewsRequest
from pydantic import BaseModel

from app.alpaca.client import AlpacaClients

logger = logging.getLogger(__name__)

# How far back each poll searches for articles -- only bounds the query
# window; what actually counts as "new" is the tracker's own seen-set, not
# this. Wide enough that one skipped/failed poll cycle (a transient error,
# a slow request) doesn't let an article fall through the gap between polls.
_LOOKBACK = timedelta(hours=6)
_BATCH_SIZE = 20


class NewsFeedItem(BaseModel):
    id: str  # f"{article_id}:{symbol}" -- dedup key and frontend list key
    article_id: str
    symbol: str
    headline: str
    source: str
    url: str | None
    published_at: datetime
    discovered_at: datetime


class NewsFeedTracker:
    """Bounded ring buffer of the most recently discovered NewsFeedItems.
    poll() is the only way items get added -- call it from exactly one
    place (ScannerEngine._refresh_news_feed) so the seen-set stays
    authoritative for what's already been broadcast.
    """

    def __init__(self, ring_buffer_size: int) -> None:
        self._ring_size = ring_buffer_size
        self._seen: set[str] = set()
        self._items: deque[NewsFeedItem] = deque()

    def recent(self, limit: int | None = None) -> list[NewsFeedItem]:
        """Newest first -- what GET /api/news-feed/recent returns to seed
        a freshly mounted widget.

        Sorted explicitly by published_at rather than trusting insertion
        (ring-buffer) order: poll()'s within-poll sort only guarantees
        articles discovered in the *same* poll come out in publish order.
        Across poll cycles that's not guaranteed -- Alpaca can surface an
        article on a later poll whose published_at predates something an
        earlier poll already recorded (syndication/backfill lag), and
        _record always appends to the end regardless. Sorting here is the
        one place that has to hold regardless of discovery order.
        """
        items = sorted(self._items, key=lambda item: item.published_at, reverse=True)
        return items[:limit] if limit is not None else items

    def _record(self, item: NewsFeedItem) -> None:
        self._items.append(item)
        self._seen.add(item.id)
        if len(self._items) > self._ring_size:
            evicted = self._items.popleft()
            self._seen.discard(evicted.id)

    async def poll(self, clients: AlpacaClients, symbols: list[str]) -> list[NewsFeedItem]:
        """Fetch recent news for `symbols`, recording and returning only
        the (article, symbol) pairs not already seen -- best-effort per
        batch, same as every other news fetch in this app (a rate limit or
        transient error degrades to fewer new items this tick, not a
        failed call).
        """
        if not symbols:
            return []
        now = datetime.now(timezone.utc)
        candidates: list[NewsFeedItem] = []
        for i in range(0, len(symbols), _BATCH_SIZE):
            batch = symbols[i : i + _BATCH_SIZE]
            try:
                request = NewsRequest(
                    symbols=",".join(batch),
                    start=now - _LOOKBACK,
                    sort=Sort.DESC,
                    limit=50,
                )
                news_set = await asyncio.to_thread(clients.news.get_news, request)
            except Exception:
                logger.exception("News feed poll failed for a batch of %d symbols", len(batch))
                continue

            # `sorted()` on the symbol intersection is just for determinism
            # when one article tags several wanted symbols -- set
            # iteration order is otherwise unspecified. Not sorted/recorded
            # yet -- collected across *every* batch first (see below),
            # since `symbols` is chunked into independent API calls and a
            # later-processed batch's articles are not necessarily older
            # than an earlier-processed batch's, even though each batch's
            # own response individually comes back newest-first.
            wanted = set(batch)
            for article in news_set.data.get("news", []):
                for symbol in sorted(wanted & set(article.symbols)):
                    key = f"{article.id}:{symbol}"
                    if key in self._seen:
                        continue
                    candidates.append(
                        NewsFeedItem(
                            id=key,
                            article_id=str(article.id),
                            symbol=symbol,
                            headline=article.headline,
                            source=article.source,
                            url=article.url or None,
                            published_at=article.created_at,
                            discovered_at=now,
                        )
                    )

        # Sorted oldest-first across the whole poll (every batch combined),
        # then recorded in that order: _record appends to the *end* of the
        # ring buffer and recent() reads it back with reversed(), which is
        # only newest-first if append order is oldest-first -- the same
        # invariant that already holds *across* polls (a later poll's
        # genuinely new articles are always newer than an earlier poll's).
        # Sorting here extends that invariant *within* one poll too, across
        # every batch it touched, not just within a single one.
        candidates.sort(key=lambda item: item.published_at)
        new_items: list[NewsFeedItem] = []
        for item in candidates:
            self._record(item)
            new_items.append(item)

        return new_items
