from datetime import date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

ET = ZoneInfo("America/New_York")
_NYSE = mcal.get_calendar("NYSE")

# Premarket/afterhours aren't official NYSE session times -- they're the
# broker-standard convention Alpaca and every retail platform use, so we
# hardcode them rather than looking for a calendar field that doesn't exist.
PREMARKET_START = time(4, 0)
AFTERHOURS_END = time(20, 0)

Session = str  # "premarket" | "regular" | "afterhours" | "closed"


@lru_cache(maxsize=16)
def trading_hours_for(day: date) -> tuple[datetime, datetime] | None:
    """(market_open, market_close) in ET for a given date, or None if it's
    not a NYSE trading day (weekend/holiday). Public so callers that need the
    exact open time (e.g. backfilling a premarket snapshot) don't have to
    hardcode 09:30 themselves.
    """
    schedule = _NYSE.schedule(start_date=day, end_date=day)
    if schedule.empty:
        return None
    market_open = schedule.market_open.iloc[0].tz_convert(ET).to_pydatetime()
    market_close = schedule.market_close.iloc[0].tz_convert(ET).to_pydatetime()
    return market_open, market_close


def current_session(now: datetime | None = None) -> Session:
    now = (now or datetime.now(ET)).astimezone(ET)
    hours = trading_hours_for(now.date())
    if hours is None:
        return "closed"
    market_open, market_close = hours

    premarket_start = datetime.combine(now.date(), PREMARKET_START, tzinfo=ET)
    afterhours_end = datetime.combine(now.date(), AFTERHOURS_END, tzinfo=ET)

    if now < premarket_start:
        return "closed"
    if now < market_open:
        return "premarket"
    if now < market_close:
        return "regular"
    if now < afterhours_end:
        return "afterhours"
    return "closed"
