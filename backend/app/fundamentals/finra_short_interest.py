"""FINRA's free, unauthenticated bulk short-interest file.

Finnhub's short-interest endpoint turned out to be paid-plan-only (403 even
with a real key -- see app.fundamentals.client history). FINRA publishes the
same underlying data for free with no API key at all, as a biweekly bulk CSV
covering both OTC *and* exchange-listed (NASDAQ/NYSE) securities -- verified
live: rows for "A" (Agilent) and "AA" (Alcoa) both carry
marketClassCode=NYSE, not just OTC, despite "otcmarket" in the URL path.

It's published on a biweekly settlement schedule (~15th and month-end,
filed a couple business days later) rather than a fixed date, so this walks
backward from today trying each day's filename until one exists. In
practice the publish lag runs well past the "couple business days" FINRA
advertises -- verified live on 2026-08-09 that 2026-07-15 was the newest
file available (2026-07-31 wasn't up yet), a 25-day gap -- so the lookback
window needs real headroom, not just one biweekly cycle.
"""

import csv
import io
import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

_URL_TEMPLATE = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{date}.csv"
_SYMBOL_COLUMN = "symbolCode"
_SHORT_QTY_COLUMN = "currentShortPositionQuantity"

# FINRA's CDN is S3-backed and returns 403 AccessDenied -- not 404 -- for a
# date with no published file (bucket listing is denied, so a missing key
# reads as "access denied" rather than "not found"). Confirmed live: the
# exact same request against a real file returns 200; against a made-up
# date it returns 403 with an <Error><Code>AccessDenied</Code> body. So 403
# here means "try the next date back", same as a genuine 404 would.
_NOT_FOUND_STATUSES = {403, 404}


def _parse_csv(text: str) -> dict[str, float]:
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    if reader.fieldnames is None or _SYMBOL_COLUMN not in reader.fieldnames:
        logger.warning(
            "FINRA short-interest CSV header missing %s -- saw columns=%s",
            _SYMBOL_COLUMN,
            reader.fieldnames,
        )
        return {}

    result: dict[str, float] = {}
    for row in reader:
        symbol = row.get(_SYMBOL_COLUMN)
        raw_qty = row.get(_SHORT_QTY_COLUMN)
        if not symbol or raw_qty is None or raw_qty == "":
            continue
        try:
            result[symbol] = float(raw_qty)
        except ValueError:
            continue
    return result


async def fetch_latest_short_interest(
    client: httpx.AsyncClient, max_lookback_days: int = 45
) -> dict[str, float]:
    """{symbol: current_short_position_quantity} from the most recently
    published file within max_lookback_days, or {} if none is found (e.g.
    FINRA's CDN is unreachable) -- callers should treat that as "keep
    whatever was cached before", not as an error to surface.
    """
    today = datetime.now(timezone.utc).date()

    for offset in range(max_lookback_days):
        date_str = (today - timedelta(days=offset)).strftime("%Y%m%d")
        url = _URL_TEMPLATE.format(date=date_str)
        try:
            resp = await client.get(url)
        except Exception:
            logger.exception("FINRA short-interest request failed for %s", url)
            continue

        if resp.status_code in _NOT_FOUND_STATUSES:
            # Expected for every date that isn't a settlement/publish date.
            continue
        if resp.status_code != 200:
            logger.warning("FINRA short-interest fetch got %s for %s", resp.status_code, url)
            continue

        parsed = _parse_csv(resp.text)
        logger.info(
            "FINRA short-interest file loaded: %s (%d symbols)", date_str, len(parsed)
        )
        return parsed

    logger.warning(
        "No FINRA short-interest file found in the last %d days", max_lookback_days
    )
    return {}
