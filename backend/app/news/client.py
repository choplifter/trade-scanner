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

logger = logging.getLogger(__name__)

_LOOKBACK = timedelta(hours=48)


class NewsItem(BaseModel):
    headline: str
    summary: str
    source: str
    url: str | None
    published_at: datetime


async def fetch_recent_news(alpaca: AlpacaClients, symbol: str, limit: int = 5) -> list[NewsItem]:
    """Best-effort: a fetch failure (rate limit, transient API error)
    degrades to no news rather than failing the caller outright, same as
    app.ai.trade_ideas._fetch_headlines.
    """
    try:
        request = NewsRequest(
            symbols=symbol,
            start=datetime.now(timezone.utc) - _LOOKBACK,
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
