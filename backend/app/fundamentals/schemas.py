from datetime import datetime

from pydantic import BaseModel


class CompanyProfile(BaseModel):
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    description: str | None = None
    website: str | None = None
    logo_url: str | None = None
    country: str | None = None


class FundamentalsData(BaseModel):
    symbol: str
    float_shares: float | None = None
    market_cap: float | None = None
    # Today's volume across the whole consolidated tape, from FMP, and the
    # 20-day average of the same. These were the only full-tape figures the
    # app held while it ran on the IEX feed. On SIP the Alpaca numbers are
    # full-tape too, and these become the independent second opinion the
    # feed-health check compares against -- see
    # FundamentalsCache.tape_coverage_pct.
    full_tape_volume: float | None = None
    full_tape_avg_volume: float | None = None
    short_interest_pct: float | None = None
    profile: CompanyProfile | None = None
    updated_at: datetime
