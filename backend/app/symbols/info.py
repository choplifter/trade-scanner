"""Company info + recent news for a single, user-selected symbol -- the
"symbol detail panel" data, as opposed to app.scanners.schemas.ScannerRow
which is the per-poll, whole-ranked-view broadcast payload. Deliberately
kept separate: this is fetched on demand for one symbol at a time, not
scoped/cached the way the scanner's fundamentals refresh is.

Called directly (not over HTTP) by both backend.app.routers.symbols and
the Dash analytics app's callbacks, same pattern already used for AI
trade ideas (see app.ai.trade_ideas.generate_trade_ideas, called from
both routers.trade_ideas and dash_app.pages.scanner_heatmap).
"""

import asyncio

from pydantic import BaseModel

from app.alpaca.client import AlpacaClients
from app.fundamentals.cache import FundamentalsCache
from app.news.client import NewsItem, fetch_recent_fmp_news, fetch_recent_news


class SymbolInfoResponse(BaseModel):
    symbol: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    description: str | None = None
    website: str | None = None
    logo_url: str | None = None
    market_cap: float | None = None
    float_shares: float | None = None
    news: list[NewsItem] = []


async def get_symbol_info(
    fundamentals: FundamentalsCache, alpaca: AlpacaClients, symbol: str
) -> SymbolInfoResponse:
    """ensure_fresh([symbol]) is the same method the scanner engine calls
    for ranked-view symbols -- it already no-ops if this symbol's cache
    entry isn't stale yet, so calling it here doesn't spend extra FMP
    quota re-fetching a symbol that's already warm, and needs no new
    cache method. News isn't cached -- Alpaca isn't the scarce resource
    here (FMP is), so a plain per-request fetch is fine.
    """
    # Both feeds, concurrently. Unlike the catalyst path -- where Alpaca is
    # authoritative and FMP only fills gaps, because the boost's calibration
    # depends on it -- this panel is display only, so more real context is
    # strictly better and the two are merged rather than ranked. Each item
    # still shows its own publisher, so the reader can weigh a company press
    # release against a commentary piece themselves.
    _, alpaca_news, fmp_news = await asyncio.gather(
        fundamentals.ensure_fresh([symbol]),
        fetch_recent_news(alpaca, symbol),
        fetch_recent_fmp_news(
            fundamentals.http_client, fundamentals.settings.fmp_api_key, symbol
        ),
    )
    news = merge_news(alpaca_news, fmp_news)
    data = fundamentals.get(symbol)
    profile = data.profile if data else None

    return SymbolInfoResponse(
        symbol=symbol,
        company_name=profile.name if profile else None,
        sector=profile.sector if profile else None,
        industry=profile.industry if profile else None,
        description=profile.description if profile else None,
        website=profile.website if profile else None,
        logo_url=profile.logo_url if profile else None,
        market_cap=data.market_cap if data else None,
        float_shares=data.float_shares if data else None,
        news=news,
    )


def merge_news(alpaca_news: list[NewsItem], fmp_news: list[NewsItem], limit: int = 6) -> list[NewsItem]:
    """Both feeds interleaved by recency, newest first.

    Deduped on the normalised headline, because a company press release is
    syndicated across wires and routinely arrives from both feeds -- without
    this the panel would show the same story twice with different publisher
    labels. Alpaca wins a tie purely for stability of display, not because
    either is more correct about a story they both carry.
    """
    seen: set[str] = set()
    merged: list[NewsItem] = []
    for item in sorted([*alpaca_news, *fmp_news], key=lambda i: i.published_at, reverse=True):
        key = " ".join(item.headline.lower().split())
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:limit]
