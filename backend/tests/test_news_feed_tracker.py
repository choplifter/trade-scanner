"""NewsFeedTracker's dedup, ranking and ring-eviction behavior -- pure over
a fake clients.news, no real Alpaca call.
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
        self.requests = []

    def get_news(self, request):
        self.requests.append(request)
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


def test_one_article_tagged_to_two_symbols_is_one_item_with_both_symbols():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients([_article(article_id=1, symbols=["AAA", "BBB"])])

    new_items = asyncio.run(tracker.poll(clients))

    assert len(new_items) == 1
    item = new_items[0]
    assert item.id == "1" and item.article_id == "1"
    assert item.symbol == "AAA" and item.symbols == ["AAA", "BBB"]
    assert item.ranked is False


def test_market_wide_poll_sends_no_symbols_and_keeps_untagged_articles():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients([_article(article_id=1, symbols=[]), _article(article_id=2, symbols=["ZZZ"])])

    new_items = asyncio.run(tracker.poll(clients))

    assert {i.id for i in new_items} == {"1", "2"}
    assert clients.news.requests[0].symbols is None
    untagged = next(i for i in new_items if i.id == "1")
    assert untagged.symbol == "" and untagged.symbols == []


def test_ranked_follows_the_scanner_set_at_read_time():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients([_article(article_id=1, symbols=["AAA"]), _article(article_id=2, symbols=["ZZZ"])])
    tracker.set_ranked({"aaa"})

    new_items = asyncio.run(tracker.poll(clients))

    assert {i.id: i.ranked for i in new_items} == {"1": True, "2": False}
    assert [i.id for i in tracker.recent(ranked_only=True)] == ["1"]
    tracker.set_ranked({"ZZZ"})
    assert [i.id for i in tracker.recent(ranked_only=True)] == ["2"]
    assert {i.id: i.ranked for i in tracker.recent()} == {"1": False, "2": True}


def test_polling_again_with_the_same_article_yields_nothing_new():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients([_article(article_id=1, symbols=["AAA"])])

    first = asyncio.run(tracker.poll(clients))
    second = asyncio.run(tracker.poll(clients))

    assert len(first) == 1
    assert second == []
    assert len(tracker.recent()) == 1


def test_record_from_the_stream_dedups_against_the_poll():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    article = _article(article_id=7, symbols=["AAA"])
    assert tracker.record(article, _NOW) is not None
    assert tracker.record(article, _NOW) is None
    assert asyncio.run(tracker.poll(_Clients([article]))) == []


def test_symbol_scoped_poll_ignores_articles_for_other_symbols():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients([_article(article_id=1, symbols=["ZZZ"])])

    new_items = asyncio.run(tracker.poll(clients, ["AAA"]))

    assert new_items == []
    assert tracker.recent() == []
    assert clients.news.requests[0].symbols == "AAA"


def test_recent_returns_newest_first():
    """recent() must reflect actual publish time, not whichever order the
    fetch happened to return articles in."""
    tracker = NewsFeedTracker(ring_buffer_size=100)
    newest = _NOW
    older = _NOW.replace(hour=10)
    clients = _Clients(
        [
            _article(article_id=2, symbols=["AAA"], headline="Older", created_at=older),
            _article(article_id=1, symbols=["AAA"], headline="Newest", created_at=newest),
        ]
    )

    asyncio.run(tracker.poll(clients))

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

    asyncio.run(tracker.poll(clients))

    ids = {i.article_id for i in tracker.recent()}
    assert ids == {"1", "2"}


def test_ordering_holds_across_batches_not_just_within_one():
    """A symbol-scoped poll is chunked into independent Alpaca calls
    (_BATCH_SIZE=20); poll() must sort by publish time across every batch
    it made this poll, not just within each individual one."""
    tracker = NewsFeedTracker(ring_buffer_size=100)
    symbols = [f"SYM{i}" for i in range(25)]  # spans two batches (20 + 5)

    class _MultiBatchNews:
        def get_news(self, request):
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


def test_recent_stays_sorted_across_separate_poll_cycles():
    tracker = NewsFeedTracker(ring_buffer_size=100)

    asyncio.run(tracker.poll(_Clients([_article(article_id=1, symbols=["AAA"], created_at=_NOW)])))
    asyncio.run(tracker.poll(_Clients([_article(article_id=2, symbols=["AAA"], created_at=_NOW.replace(hour=6))])))

    ids = [i.article_id for i in tracker.recent()]
    assert ids == ["1", "2"]


def test_recent_respects_limit():
    tracker = NewsFeedTracker(ring_buffer_size=100)
    clients = _Clients([_article(article_id=i, symbols=["AAA"]) for i in range(1, 6)])

    asyncio.run(tracker.poll(clients))

    assert len(tracker.recent(limit=2)) == 2


def test_an_empty_symbol_list_short_circuits_without_calling_the_api():
    tracker = NewsFeedTracker(ring_buffer_size=100)

    class _ExplodingClients:
        class _News:
            def get_news(self, request):
                raise AssertionError("should not be called for an empty symbol list")

        news = _News()

    new_items = asyncio.run(tracker.poll(_ExplodingClients(), []))
    assert new_items == []
