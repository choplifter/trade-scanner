from datetime import datetime

from pydantic import BaseModel


class ScannerRow(BaseModel):
    symbol: str
    exchange: str = ""
    last_price: float
    prev_close: float
    pct_change: float
    # The *overnight* gap: today's open against yesterday's close. Distinct
    # from pct_change, which is the current price against yesterday's close
    # and therefore includes everything that has happened since the open. A
    # stock that gapped +20% and has since given it all back reads +20% here
    # and ~0% there; screening "gapped hard this morning" needs this one.
    # None before today's open has printed.
    gap_pct: float | None = None
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
    # True when pct_change_last_15m is at least settings.alarm_momentum_pct_threshold
    # *and* the latest 1-minute candle confirms it (shaved top, green, and
    # trading above session VWAP) -- long setups only, see
    # app.scanners.formulas.is_momentum_alert. Drives the frontend's
    # momentum-alarm overlay (off by default).
    is_momentum_alert: bool = False
    # Volume *rate* rather than running total -- see
    # app.market_data.volume_surge. rvol (above) is cumulative since the open
    # and only ever climbs, so it can't distinguish a stock that ran at 09:45
    # and died from one accelerating into the close.
    #
    # volume_1h: shares traded in the trailing window.
    # volume_surge: that window over the one before it. Honest as a
    #   description, misleading as a filter -- session volume is U-shaped, so
    #   near the close this is above 1 for most of the market at once.
    # rvol_1h: that window over what the same *clock window* normally trades
    #   (from the intraday volume profile). "2x" means twice the usual
    #   15:00-16:00 volume, not twice a quiet lunch hour. This is the one to
    #   screen on.
    #
    # All None outside the regular session and until the momentum cache has
    # fetched -- there is no "right now" to measure when the market is shut.
    volume_1h: float | None = None
    volume_surge: float | None = None
    rvol_1h: float | None = None
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
