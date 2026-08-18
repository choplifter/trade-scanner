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
    # 20-day average of the same. The Alpaca IEX feed only reports the slice
    # of trades that routed through IEX, so these are the only full-tape
    # figures the app holds -- see FundamentalsCache.tape_coverage for what
    # they are used for.
    full_tape_volume: float | None = None
    full_tape_avg_volume: float | None = None
    short_interest_pct: float | None = None
    profile: CompanyProfile | None = None
    updated_at: datetime
