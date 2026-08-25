"""Recent news headlines for a single symbol, via Alpaca's NewsClient --
the same client app.ai.trade_ideas._fetch_headlines uses, but keeping
more than one article and more than just the headline per article.
Content (the full article body) is deliberately not requested/kept --
headline/summary/source/url/published_at is the right amount for a
sidebar without reproducing a third party's full article text.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

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

# The scanner's catalyst window (app.market_data.news.DEFAULT_NEWS_LOOKBACK):
# a flat 48h, the baseline _CATALYST_BOOST was calibrated against and
# deliberately not changed there.
_LOOKBACK = timedelta(hours=48)


def symbol_news_cutoff(now: datetime | None = None) -> datetime:
    """How far back the symbol panel (and the chart's news pins) look.

    The earlier of the two windows this app uses, so that a story the
    scanner is flagging as *the* catalyst can never be missing from the
    panel that is supposed to explain the move:

    - recent_news_cutoff(): session-anchored, so a Monday panel still
      reaches Friday's session instead of stopping at Saturday.
    - the scanner's flat 48h: on an ordinary weekday afternoon this reaches
      ~27h further back than the session anchor does (today's open less an
      18h buffer starts at ~15:30 ET *yesterday*).

    Observed on BCTX, 2026-08-25: Monday's 07:33 ET press release drove the
    scanner's catalyst badge (inside 48h) while /symbols/BCTX/info returned
    no news at all (before the session anchor), so the chart had nothing to
    pin. Taking the earlier of the two closes that gap; the chart's own
    guard against stories older than its first loaded bar keeps the wider
    window from cluttering a chart that does not reach back that far.
    """
    now = now or datetime.now(UTC)
    return min(recent_news_cutoff(now), now - _LOOKBACK)


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


async def fetch_recent_news(
    alpaca: AlpacaClients, symbol: str, limit: int = 5
) -> list[NewsItem]:
    """Best-effort: a fetch failure (rate limit, transient API error)
    degrades to no news rather than failing the caller outright, same as
    app.ai.trade_ideas._fetch_headlines.
    """
    try:
        request = NewsRequest(
            symbols=symbol,
            # The wider of session-anchored and flat-48h -- see
            # symbol_news_cutoff. Shared with the FMP half below so one
            # panel doesn't apply two different notions of "recent".
            start=symbol_news_cutoff(),
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
    # Same cutoff as the Alpaca half above. Without one this panel is headed
    # "Recent News" while showing whatever FMP has, which for a quiet name
    # is months old -- CPRT's four Business Wire items were all stale, and a
    # stale story sitting under that heading reads as the explanation for
    # today's move.
    cutoff = symbol_news_cutoff()
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
