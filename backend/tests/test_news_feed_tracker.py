"""NewsFeedTracker's dedup/ring-eviction behavior -- pure over a fake
clients.news, no real Alpaca call.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from app.market_data.news_feed import NewsFeedTracker


@dataclass
class _Article:
    id: int
    headline: str
    source: str
    url: str | None
    created_at: datetime
    symbols: list[str]


class _NewsSet:
    def __init__(self, articles):
        self.data = {"news": articles}


class _News:
    def __init__(self, articles):
        self._articles = articles

    def get_news(self, request):
        return _NewsSet(self._articles)


class _Clients:
    def __init__(self, articles):
        self.news = _News(articles)


_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def _article(article_id=1, symbols=("AAA",), headline="Big news", created_at=_NOW):
    return _Article(
        id=article_id, headline=headline, source="Benzinga", url="https://example.com/1",
        created_at=created_at, symbols=list(symbols),
    )


def test_one_article_tagged_to_two_symbols_yields_two_items():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients([_article(article_id=1, symbols=["AAA", "BBB"])])

    new_items = asyncio.run(tracker.poll(clients, ["AAA", "BBB"]))

    assert {i.symbol for i in new_items} == {"AAA", "BBB"}
    assert all(i.article_id == "1" for i in new_items)
    assert new_items[0].id != new_items[1].id


def test_polling_again_with_the_same_article_yields_nothing_new():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients([_article(article_id=1, symbols=["AAA"])])

    first = asyncio.run(tracker.poll(clients, ["AAA"]))
    second = asyncio.run(tracker.poll(clients, ["AAA"]))

    assert len(first) == 1
    assert second == []
    assert len(tracker.recent()) == 1


def test_an_article_not_tagged_to_a_requested_symbol_is_ignored():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients([_article(article_id=1, symbols=["ZZZ"])])

    new_items = asyncio.run(tracker.poll(clients, ["AAA"]))

    assert new_items == []
    assert tracker.recent() == []


def test_recent_returns_newest_first():
    """recent() must reflect actual publish time, not whichever order the
    fetch happened to return articles in -- the fake _News.get_news below
    (like a real single Alpaca call) returns them in an arbitrary/DESC
    order that poll() must not trust blindly."""
    tracker = NewsFeedTracker(ring_buffer_size=100)
    newest = _NOW
    older = _NOW.replace(hour=10)
    # Deliberately fed *out* of chronological order (older first) to prove
    # poll() sorts by published_at itself rather than trusting input order.
    clients = _Clients(
        [
            _article(article_id=2, symbols=["AAA"], headline="Older", created_at=older),
            _article(article_id=1, symbols=["AAA"], headline="Newest", created_at=newest),
        ]
    )

    asyncio.run(tracker.poll(clients, ["AAA"]))

    ids = [i.article_id for i in tracker.recent()]
    assert ids[0] == "1"
    assert ids[-1] == "2"


def test_ring_buffer_evicts_the_oldest_item_once_full():
    tracker = NewsFeedTracker(ring_buffer_size=2)
    clients = _Clients(
        [
            _article(article_id=1, symbols=["AAA"], created_at=_NOW.replace(hour=12)),
            _article(article_id=2, symbols=["AAA"], created_at=_NOW.replace(hour=11)),
            _article(article_id=3, symbols=["AAA"], created_at=_NOW.replace(hour=10)),
        ]
    )

    asyncio.run(tracker.poll(clients, ["AAA"]))

    # A ring of size 2 should keep the two genuinely newest (1, 2) and
    # evict the genuinely oldest one (3).
    ids = {i.article_id for i in tracker.recent()}
    assert ids == {"1", "2"}


def test_ordering_holds_across_batches_not_just_within_one():
    """symbols is chunked into independent Alpaca calls (_BATCH_SIZE=20).
    A later-processed batch's articles are not necessarily older than an
    earlier-processed batch's -- poll() must sort by publish time across
    every batch it made this poll, not just within each individual one."""
    tracker = NewsFeedTracker(ring_buffer_size=100)
    symbols = [f"SYM{i}" for i in range(25)]  # spans two batches (20 + 5)

    class _MultiBatchNews:
        def get_news(self, request):
            # First batch call (SYM0..SYM19) answers with an old article;
            # second batch call (SYM20..SYM24) answers with a new one --
            # the reverse of "later-processed batch is newer".
            if "SYM20" in request.symbols:
                return _NewsSet([_article(article_id=2, symbols=["SYM20"], created_at=_NOW)])
            return _NewsSet(
                [_article(article_id=1, symbols=["SYM0"], created_at=_NOW.replace(hour=6))]
            )

    class _MultiBatchClients:
        news = _MultiBatchNews()

    asyncio.run(tracker.poll(_MultiBatchClients(), symbols))

    ids = [i.article_id for i in tracker.recent()]
    assert ids == ["2", "1"]
    assert len(tracker.recent()) == 2


def test_recent_respects_limit():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients(
        [_article(article_id=i, symbols=["AAA"]) for i in range(1, 6)]
    )

    asyncio.run(tracker.poll(clients, ["AAA"]))

    assert len(tracker.recent(limit=2)) == 2


def test_no_symbols_short_circuits_without_calling_the_api():
    tracker = NewsFeedTracker(ring_buffer_size=100)

    class _ExplodingClients:
        class _News:
            def get_news(self, request):
                raise AssertionError("should not be called for an empty symbol list")

        news = _News()

    new_items = asyncio.run(tracker.poll(_ExplodingClients(), []))
    assert new_items == []
