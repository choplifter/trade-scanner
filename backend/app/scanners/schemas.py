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
    # % price change over just the trailing 15 minutes, refreshed on a slow
    # cadence for whatever's currently ranked -- see
    # app.scanners.momentum_cache.MomentumCache. Distinct from pct_change
    # (since prior close): a symbol can have a huge pct_change from earlier
    # in the session while this reads ~0 because it's since gone flat, or
    # vice versa for a fresh late-session breakout. None until fetched, or
    # when there isn't yet 15 minutes of bars to compare against.
    pct_change_last_15m: float | None = None
    # True when pct_change_last_15m exceeds settings.alarm_momentum_pct_threshold
    # in magnitude *and* the latest 1-minute candle is a marubozu (almost no
    # wick) -- see app.scanners.formulas.is_momentum_alert. Drives the
    # frontend's momentum-alarm overlay (off by default).
    is_momentum_alert: bool = False
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
    # True when rvol > formulas._FADE_RISK_RVOL -- this app's own
    # scanner_history.sqlite3 win-rate analysis found extreme RVOL predicts
    # a *worse* outcome (25.6% win rate, -10.38% avg return), consistent
    # with "gap and crap" exhaustion rather than continuation. Ranking
    # already discounts for this (see formulas.rank_score); this field lets
    # the frontend also warn the user directly rather than only showing it
    # indirectly via rank order. See app.scanners.formulas.is_fade_risk.
    is_fade_risk: bool = False
    updated_at: datetime


class ScannerUpdateMessage(BaseModel):
    type: str = "scanner_update"
    scanner: str
    session: str
    rows: list[ScannerRow]
