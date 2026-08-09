from datetime import datetime

from pydantic import BaseModel


class CompanyProfile(BaseModel):
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    description: str | None = None
    website: str | None = None
    logo_url: str | None = None


class FundamentalsData(BaseModel):
    symbol: str
    float_shares: float | None = None
    market_cap: float | None = None
    short_interest_pct: float | None = None
    profile: CompanyProfile | None = None
    updated_at: datetime
