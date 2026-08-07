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
    updated_at: datetime


class ScannerUpdateMessage(BaseModel):
    type: str = "scanner_update"
    scanner: str
    session: str
    rows: list[ScannerRow]
