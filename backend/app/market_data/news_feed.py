"""In-memory tracker for the live news feed -- the NewsFeedWidget's wire of
the newest articles across the whole market, with the ones tagged to a
symbol currently ranked in a fixed scanner view marked as such.

Two sources feed it, both through record(): Alpaca's news websocket
(app.market_data.news_stream, every article the moment it is published)
and a once-a-minute market-wide poll (ScannerEngine._refresh_news_feed),
which seeds the buffer at startup and catches anything the stream missed
while reconnecting. Dedup is per article id, so the two never double an
entry.

Not persisted -- same "personal-dashboard live feed, not a compliance
record" rationale as app.scanners.benchmark_tracker.ScannerBenchmarkTracker.
A server restart just means the feed starts over from the first poll.

One entry per article, not per (article, symbol) pair: a market-wide
article often names several symbols (or none), and one row with symbol
chips reads better than the same headline three times. `symbol` is the
first tagged symbol, the row's click and drag target.
"""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Iterable

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
# Alpaca's NewsRequest caps at 50 articles per call.
_PAGE_LIMIT = 50


class NewsFeedItem(BaseModel):
    id: str  # the article id -- dedup key and frontend list key
    article_id: str
    # The first tagged symbol ("" when the article names none): the row's
    # click/drag target. Every tagged symbol is in `symbols`.
    symbol: str
    symbols: list[str]
    # Tagged to a symbol ranked in a fixed scanner view right now.
    ranked: bool
    headline: str
    source: str
    url: str | None
    published_at: datetime
    discovered_at: datetime


class NewsFeedTracker:
    """Bounded ring buffer of the most recently discovered articles.
    record() is the only way items get added -- the stream handler and
    the poll both go through it, so the seen-set stays authoritative for
    what's already been broadcast.
    """

    def __init__(self, ring_buffer_size: int) -> None:
        self._ring_size = ring_buffer_size
        self._seen: set[str] = set()
        self._items: deque[NewsFeedItem] = deque()
        self._ranked: set[str] = set()

    # --- the ranked set ---------------------------------------------------------

    def set_ranked(self, symbols: Iterable[str]) -> None:
        """The symbols currently ranked in the fixed scanner views -- what
        `ranked` on an item means. Updated by the scanner engine each poll."""
        self._ranked = {s.upper() for s in symbols}

    def is_ranked(self, symbols: Iterable[str]) -> bool:
        return any(s.upper() in self._ranked for s in symbols)

    # --- reads ------------------------------------------------------------------

    def recent(self, limit: int | None = None, ranked_only: bool = False) -> list[NewsFeedItem]:
        """Newest first -- what GET /api/news-feed/recent returns to seed
        a freshly mounted widget. `ranked` is re-evaluated against the
        ranked set as it stands now, not as it stood at discovery.

        Sorted explicitly by published_at rather than trusting insertion
        (ring-buffer) order: Alpaca can surface an article on a later poll
        whose published_at predates something already recorded
        (syndication/backfill lag), and _record always appends to the end.
        """
        items = [item.model_copy(update={"ranked": self.is_ranked(item.symbols)}) for item in self._items]
        if ranked_only:
            items = [item for item in items if item.ranked]
        items.sort(key=lambda item: item.published_at, reverse=True)
        return items[:limit] if limit is not None else items

    # --- writes -----------------------------------------------------------------

    def _record(self, item: NewsFeedItem) -> None:
        self._items.append(item)
        self._seen.add(item.id)
        if len(self._items) > self._ring_size:
            evicted = self._items.popleft()
            self._seen.discard(evicted.id)

    def record(self, article, now: datetime | None = None) -> NewsFeedItem | None:
        """One Alpaca News (SDK model or duck-typed) into the buffer; the
        item if it is new, None if already seen."""
        key = str(article.id)
        if key in self._seen:
            return None
        symbols = [str(s).upper() for s in (getattr(article, "symbols", None) or []) if s]
        item = NewsFeedItem(
            id=key,
            article_id=key,
            symbol=symbols[0] if symbols else "",
            symbols=symbols,
            ranked=self.is_ranked(symbols),
            headline=article.headline,
            source=getattr(article, "source", None) or "",
            url=getattr(article, "url", None) or None,
            published_at=article.created_at,
            discovered_at=now or datetime.now(timezone.utc),
        )
        self._record(item)
        return item

    async def poll(self, clients: AlpacaClients, symbols: list[str] | None = None) -> list[NewsFeedItem]:
        """Fetch recent news -- market-wide when `symbols` is None, for
        those symbols otherwise -- recording and returning only the
        articles not already seen. Best-effort per request, same as every
        other news fetch in this app: a rate limit or transient error
        degrades to fewer new items this tick, not a failed call.
        """
        if symbols is not None and not symbols:
            return []
        now = datetime.now(timezone.utc)
        articles: list = []
        batches: list[list[str] | None] = (
            [None] if symbols is None else [symbols[i : i + _BATCH_SIZE] for i in range(0, len(symbols), _BATCH_SIZE)]
        )
        for batch in batches:
            try:
                request = NewsRequest(
                    symbols=",".join(batch) if batch else None,
                    start=now - _LOOKBACK,
                    sort=Sort.DESC,
                    limit=_PAGE_LIMIT,
                )
                news_set = await asyncio.to_thread(clients.news.get_news, request)
            except Exception:
                logger.exception("News feed poll failed (%s)", "market-wide" if batch is None else f"{len(batch)} symbols")
                continue
            wanted = set(batch) if batch else None
            for article in news_set.data.get("news", []):
                if wanted is not None and not (wanted & set(article.symbols or [])):
                    continue
                articles.append(article)

        # Oldest first across every request of this poll, then recorded in
        # that order: _record appends to the end of the ring buffer, and an
        # eviction must drop the genuinely oldest article.
        articles.sort(key=lambda a: a.created_at)
        new_items: list[NewsFeedItem] = []
        for article in articles:
            item = self.record(article, now)
            if item is not None:
                new_items.append(item)
        return new_items
