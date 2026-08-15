"""Shared Alpaca news-headline lookup -- used by both AI trade ideas
(app.ai.trade_ideas) and the scanner history store's new-appearance
recording (app.scanners.engine), so the API call and its best-effort
failure handling live in exactly one place.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from alpaca.common.enums import Sort
from alpaca.data.requests import NewsRequest

from app.alpaca.client import AlpacaClients

logger = logging.getLogger(__name__)

DEFAULT_NEWS_LOOKBACK = timedelta(hours=48)

# Benzinga publishes recurring "N <sector> Stocks <topic>" roundups
# covering a dozen-ish symbols at once -- "N <sector> Stocks Moving In
# <Day>'s <Session> Session" is the most common, but the same numbered-
# listicle template covers other topics too (verified live: "N <sector>
# Stocks With Whale Alerts In Today's Session", "N <sector> Stocks Whale
# Activity In Today's Session"), so this matches the shared structural
# prefix rather than the specific topic word -- narrower per-topic
# patterns would just keep missing whatever the next variant is. Only the
# "Moving" variant's direction is ambiguous from the headline alone (shows
# up in the body instead, split into "Gainers"/"Losers" sections); the
# other topics don't have a meaningful per-symbol direction to begin with,
# so classify_mover below just harmlessly finds nothing for those. Matched
# against the plain headline text (already fetched for free in the first
# pass below), before deciding whether the pricier include_content=True
# follow-up fetch is worth making at all.
_ROUNDUP_HEADLINE = re.compile(r"^\d+\s+.+?\bStocks\b", re.IGNORECASE)

# A second, more common Benzinga roundup template: "<Company>, <Company>,
# <Company> And Other (Big) Stocks Moving Higher/Lower On/In <day/session>."
# Unlike _ROUNDUP_HEADLINE above, the direction is already stated in the
# headline itself (one blanket direction for the whole list), so this
# doesn't need the include_content=True follow-up -- it's only used to
# recognize that a headline naming our symbol is still just a mention in a
# multi-stock list, not a story *about* that symbol, for is_roundup_headline
# below.
_ROUNDUP_TAIL = re.compile(r"And Other (Big )?Stocks Moving (Higher|Lower)", re.IGNORECASE)


def is_roundup_headline(headline: str) -> bool:
    """True for a Benzinga movers-roundup headline (either template above)
    -- a symbol being *listed* alongside a dozen others because it happened
    to move today isn't a catalyst *for* that symbol the way an earnings
    beat or FDA decision is, even though it's real, current news. Used to
    keep roundup mentions out of ranking's catalyst boost (see
    app.scanners.engine._has_headline) while still surfacing them as
    recent_headline for display -- they're not wrong, just not evidence of
    *why* the stock is moving.
    """
    return bool(_ROUNDUP_HEADLINE.match(headline) or _ROUNDUP_TAIL.search(headline))


def _classify_mover(content: str, symbol: str) -> str | None:
    """"Gainer" or "Loser" if `symbol`'s ticker link can be located in a
    Benzinga roundup's HTML content relative to its "<h3>Losers</h3>"
    marker, else None (unrecognized structure, or symbol not actually
    named in this particular article despite being in its `symbols` list --
    Benzinga tags articles more broadly than the body always covers).
    """
    if not content:
        return None
    idx = content.find(f'/quote/{symbol}"')
    if idx == -1:
        return None
    losers_idx = content.find("<h3>Losers</h3>")
    if losers_idx == -1:
        return "Gainer" if "<h3>Gainers</h3>" in content else None
    return "Loser" if idx > losers_idx else "Gainer"


async def _append_mover_directions(
    alpaca: AlpacaClients, headlines: dict[str, str], roundup_symbols: set[str], lookback: timedelta
) -> None:
    """One follow-up include_content=True fetch covering every roundup_symbols
    at once (not one call per symbol, and not per article -- symbols sharing
    the same roundup article are resolved together in a single pass over its
    `symbols` list), appending "(Gainer)"/"(Loser)" to each headline this
    can actually classify. Best-effort: on failure, headlines just keep
    their unclassified roundup text rather than being blocked on this.
    """
    try:
        request = NewsRequest(
            symbols=",".join(sorted(roundup_symbols)),
            start=datetime.now(timezone.utc) - lookback,
            sort=Sort.DESC,
            limit=50,
            include_content=True,
        )
        news_set = await asyncio.to_thread(alpaca.news.get_news, request)
    except Exception:
        logger.exception("News content fetch failed for Benzinga mover classification")
        return

    remaining = set(roundup_symbols)
    for article in news_set.data.get("news", []):
        if not remaining:
            break
        for symbol in remaining & set(article.symbols):
            direction = _classify_mover(article.content, symbol)
            if direction:
                headlines[symbol] = f"{headlines[symbol]} ({direction})"
        remaining -= set(article.symbols)


async def fetch_headlines(
    alpaca: AlpacaClients, symbols: list[str], lookback: timedelta = DEFAULT_NEWS_LOOKBACK
) -> dict[str, str]:
    """Most recent news headline per symbol, best-effort: a fetch failure
    (rate limit, transient API error) degrades to no headlines rather than
    failing the caller outright. Benzinga's numbered-listicle roundups (see
    _ROUNDUP_HEADLINE) get a "(Gainer)"/"(Loser)" suffix once classified --
    see _append_mover_directions -- fetched only for symbols that actually
    matched a roundup headline in this batch, not on every call, since
    include_content=True returns the full article body (a few KB) instead
    of just a headline. A no-op for roundup topics that don't have a
    Gainers/Losers split in the body (e.g. whale-alert roundups) --
    _classify_mover just finds nothing and the headline keeps its plain
    unclassified text.
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
    roundup_symbols: set[str] = set()
    for article in news_set.data.get("news", []):
        for symbol in article.symbols:
            if symbol in wanted and symbol not in headlines:
                headlines[symbol] = article.headline
                if _ROUNDUP_HEADLINE.match(article.headline):
                    roundup_symbols.add(symbol)

    if roundup_symbols:
        await _append_mover_directions(alpaca, headlines, roundup_symbols, lookback)

    return headlines
