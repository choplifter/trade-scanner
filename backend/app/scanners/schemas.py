from datetime import datetime

from pydantic import BaseModel


class ScannerRow(BaseModel):
    symbol: str
    exchange: str = ""
    last_price: float
    prev_close: float
    pct_change: float
    volume_today: float
    avg_vol_20d: float
    rvol: float
    dollar_volume_today: float
    day_high: float | None = None
    day_low: float | None = None
    is_hod: bool = False
    is_lod: bool = False
    spread_pct: float | None = None
    # Optional -- populated from app.fundamentals when FMP_API_KEY is
    # configured; None otherwise (Alpaca doesn't expose any of these).
    # See app.fundamentals.cache.FundamentalsCache.
    float_shares: float | None = None
    market_cap: float | None = None
    short_interest_pct: float | None = None
    country: str | None = None
    company_name: str | None = None
    # Most recent news headline, if any, refreshed on a slow cadence for
    # whatever's currently ranked -- see app.market_data.news_cache.NewsCache.
    recent_headline: str | None = None
    # When the underlying trade/bar backing last_price was actually printed
    # -- distinct from updated_at, which is when *we* last recomputed the
    # row (that happens every poll tick regardless of whether the feed
    # reported anything new). None means there's no live-confirmed price
    # at all (only a stale previous-close fallback). See
    # app.scanners.formulas.is_stale.
    last_trade_at: datetime | None = None
    # True when last_trade_at is missing or older than
    # settings.scanner_stale_row_seconds -- the row still ranks normally
    # (a thin/illiquid name going quiet for a while is real market
    # behavior, not something to hide), but the frontend uses this to warn
    # that the price shown may not reflect what the symbol is actually
    # doing right now. See app.scanners.formulas.is_stale.
    is_stale: bool = False
    updated_at: datetime


class ScannerUpdateMessage(BaseModel):
    type: str = "scanner_update"
    scanner: str
    session: str
    rows: list[ScannerRow]
