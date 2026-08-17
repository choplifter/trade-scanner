"""Recent news headlines for a single symbol, via Alpaca's NewsClient --
the same client app.ai.trade_ideas._fetch_headlines uses, but keeping
more than one article and more than just the headline per article.
Content (the full article body) is deliberately not requested/kept --
headline/summary/source/url/published_at is the right amount for a
sidebar without reproducing a third party's full article text.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from alpaca.common.enums import Sort
from alpaca.data.requests import NewsRequest
from pydantic import BaseModel

from app.alpaca.client import AlpacaClients
from app.market_data.fmp_news import (
    is_low_signal_headline,
    parse_published,
    recent_news_cutoff,
)

logger = logging.getLogger(__name__)

# Retained for reference; the fetch now uses recent_news_cutoff() instead --
# see the comment at its use site. Deliberately NOT changed in
# app.market_data.news, whose 48h window is the baseline _CATALYST_BOOST was
# calibrated against.
_LOOKBACK = timedelta(hours=48)

_FMP_URL = "https://financialmodelingprep.com/stable/news/stock"
# Over-fetch relative to `limit`: ~83% of raw FMP items are filtered out, so
# asking for exactly `limit` would usually return nothing usable.
_FMP_FETCH_LIMIT = 60


class NewsItem(BaseModel):
    headline: str
    summary: str
    source: str
    url: str | None
    published_at: datetime
    # Which feed supplied it: "alpaca" or "fmp". `source` is the article's
    # own publisher (Benzinga, GlobeNewsWire, ...) and is what the panel
    # shows; this is the provenance, kept because the two feeds have very
    # different reliability -- see app.market_data.fmp_news on FMP's noise
    # rate and its observed symbol mis-tagging.
    feed: str = "alpaca"


async def fetch_recent_news(alpaca: AlpacaClients, symbol: str, limit: int = 5) -> list[NewsItem]:
    """Best-effort: a fetch failure (rate limit, transient API error)
    degrades to no news rather than failing the caller outright, same as
    app.ai.trade_ideas._fetch_headlines.
    """
    try:
        request = NewsRequest(
            symbols=symbol,
            # Session-anchored, not a flat 48h back from now: measured on a
            # Monday a flat window reaches only to Saturday and hides the
            # whole of Friday's session. Shared with the FMP half below so
            # one panel doesn't apply two different notions of "recent".
            start=recent_news_cutoff(),
            sort=Sort.DESC,
            limit=limit,
        )
        news_set = await asyncio.to_thread(alpaca.news.get_news, request)
    except Exception:
        logger.exception("News fetch failed for %s", symbol)
        return []

    return [
        NewsItem(
            headline=article.headline,
            summary=article.summary,
            source=article.source,
            url=article.url,
            published_at=article.created_at,
        )
        for article in news_set.data.get("news", [])[:limit]
    ]


async def fetch_recent_fmp_news(
    http_client, api_key: str, symbol: str, limit: int = 5
) -> list[NewsItem]:
    """The same shape as fetch_recent_news, from FMP.

    Exists because Alpaca's Benzinga feed is thin on the small and mid caps
    this scanner surfaces -- measured over 12 scanner symbols, Alpaca had a
    story for 3 and FMP for all 12. The chart panel is where a trader asks
    "why is this moving", so an empty panel on a runner is the worst place
    for that gap to land.

    Filtered by fmp_news.is_low_signal_headline, exactly as the catalyst
    path is: FMP's raw feed is ~30% securities-litigation notices, and a
    sidebar filled with "INVESTOR ALERT: law firm reminds investors" tells
    you nothing about why a stock moved today.
    """
    if not api_key:
        return []
    # Same session-anchored cutoff the scanner flag uses. Without it this
    # panel is headed "Recent News" while showing whatever FMP has, which for
    # a quiet name is months old -- CPRT's four Business Wire items were all
    # stale, and a stale story sitting under that heading reads as the
    # explanation for today's move.
    cutoff = recent_news_cutoff()
    try:
        response = await http_client.get(
            _FMP_URL,
            params={"symbols": symbol, "limit": _FMP_FETCH_LIMIT, "apikey": api_key},
        )
        response.raise_for_status()
        items = response.json()
    except Exception:
        logger.exception("FMP news fetch failed for %s", symbol)
        return []

    if not isinstance(items, list):
        return []

    results: list[NewsItem] = []
    for item in items:
        title = (item.get("title") or "").strip()
        publisher = item.get("publisher")
        if item.get("symbol") != symbol or not title:
            continue
        if is_low_signal_headline(title, publisher):
            continue
        published = _parse_fmp_date(item.get("publishedDate"))
        if published is None or published < cutoff:
            continue
        results.append(
            NewsItem(
                headline=title,
                # FMP calls the body "text"; truncated the same way a summary
                # would be, and deliberately not the full article -- same
                # reasoning as the Alpaca path not requesting content.
                summary=(item.get("text") or "")[:400],
                source=publisher or "FMP",
                url=item.get("url"),
                published_at=published,
                feed="fmp",
            )
        )
        if len(results) >= limit:
            break
    return results


def _parse_fmp_date(value: str | None) -> datetime | None:
    """Shared with app.market_data.fmp_news.parse_published -- FMP's naive
    timestamps are US/Eastern, not UTC. See that function for the evidence;
    getting it wrong shifts every FMP story in this panel four hours earlier
    than it happened, and mis-orders it against Alpaca's aware timestamps.
    """
    return parse_published(value)
