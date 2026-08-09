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
    # Optional -- populated from app.fundamentals when FMP_API_KEY/
    # FINNHUB_API_KEY are configured; None otherwise (Alpaca doesn't expose
    # any of these). See app.fundamentals.cache.FundamentalsCache.
    float_shares: float | None = None
    market_cap: float | None = None
    short_interest_pct: float | None = None
    updated_at: datetime


class ScannerUpdateMessage(BaseModel):
    type: str = "scanner_update"
    scanner: str
    session: str
    rows: list[ScannerRow]
