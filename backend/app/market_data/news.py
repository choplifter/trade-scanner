"""Shared Alpaca news-headline lookup -- used by both AI trade ideas
(app.ai.trade_ideas) and the scanner history store's new-appearance
recording (app.scanners.engine), so the API call and its best-effort
failure handling live in exactly one place.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from alpaca.common.enums import Sort
from alpaca.data.requests import NewsRequest

from app.alpaca.client import AlpacaClients

logger = logging.getLogger(__name__)

DEFAULT_NEWS_LOOKBACK = timedelta(hours=48)


async def fetch_headlines(
    alpaca: AlpacaClients, symbols: list[str], lookback: timedelta = DEFAULT_NEWS_LOOKBACK
) -> dict[str, str]:
    """Most recent news headline per symbol, best-effort: a fetch failure
    (rate limit, transient API error) degrades to no headlines rather than
    failing the caller outright.
    """
    if not symbols:
        return {}
    try:
        request = NewsRequest(
            symbols=",".join(symbols),
            start=datetime.now(timezone.utc) - lookback,
            sort=Sort.DESC,
            limit=50,
        )
        news_set = await asyncio.to_thread(alpaca.news.get_news, request)
    except Exception:
        logger.exception("News fetch failed")
        return {}

    wanted = set(symbols)
    headlines: dict[str, str] = {}
    for article in news_set.data.get("news", []):
        for symbol in article.symbols:
            if symbol in wanted and symbol not in headlines:
                headlines[symbol] = article.headline
    return headlines
