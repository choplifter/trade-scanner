from datetime import datetime

from pydantic import BaseModel


class FundamentalsData(BaseModel):
    symbol: str
    float_shares: float | None = None
    market_cap: float | None = None
    short_interest_pct: float | None = None
    updated_at: datetime
