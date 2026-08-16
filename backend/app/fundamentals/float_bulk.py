"""FMP's bulk shares-float file: float for every symbol in a handful of calls.

The per-symbol `/stable/shares-float` endpoint (see client.py) is why float was
only ever fetched for symbols already in a ranked view -- a few dozen at a time,
to stay inside the free tier's daily cap. That made float useless as a *ranking*
input: ranking on float needs float before ranking, and float only arrived after.

`/stable/shares-float-all` resolves that. Verified live on a Starter
subscription: 8 pages at limit=5000 covers ~35.3k symbols, so the entire
universe's float costs ~8 requests against Starter's 300/minute rather than one
request per symbol. Note the sibling `/stable/profile-bulk` is 402 Restricted on
Starter, so company profile/market cap still go per symbol -- only float is
bulk-fetchable here.

The file spans far more than US equities (Morningstar-style IDs like
"0P00016G44" appear alongside real tickers) and only ~71% of rows carry a
non-zero float, so callers should treat a missing symbol as "unknown float"
rather than an error.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_URL = "https://financialmodelingprep.com/stable/shares-float-all"
# 5000 is the largest page size observed to be honoured; the whole file is ~8
# pages at that size.
_PAGE_SIZE = 5000
# Hard stop so an endpoint that starts returning full pages forever can't spin
# indefinitely. ~2x the observed page count, to leave room for the list growing.
_MAX_PAGES = 20


async def fetch_all_float(client: httpx.AsyncClient, api_key: str) -> dict[str, float]:
    """{symbol: float_shares} for every symbol FMP reports a non-zero float for.

    Returns {} on failure rather than raising -- callers should treat that as
    "keep whatever was cached before", same convention as
    finra_short_interest.fetch_latest_short_interest.
    """
    result: dict[str, float] = {}
    for page in range(_MAX_PAGES):
        try:
            resp = await client.get(
                _URL, params={"page": page, "limit": _PAGE_SIZE, "apikey": api_key}
            )
        except Exception:
            logger.exception("Bulk shares-float request failed on page %d", page)
            break

        if resp.status_code != 200:
            # 402 here would mean the plan doesn't include this endpoint; log it
            # once with the status so it's diagnosable rather than silent.
            logger.warning(
                "Bulk shares-float got HTTP %s on page %d -- stopping", resp.status_code, page
            )
            break

        try:
            rows = resp.json()
        except ValueError:
            logger.warning("Bulk shares-float returned non-JSON on page %d", page)
            break
        if not isinstance(rows, list) or not rows:
            break

        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            raw = row.get("floatShares")
            if not symbol or raw in (None, ""):
                continue
            try:
                shares = float(raw)
            except (TypeError, ValueError):
                continue
            # A zero float is FMP saying "unknown", not a real zero-float
            # security -- keeping it would make every such symbol look like the
            # lowest-float name on the board.
            if shares > 0:
                result[symbol] = shares

        if len(rows) < _PAGE_SIZE:
            break
    else:
        logger.warning("Bulk shares-float hit the %d-page cap -- list may be truncated", _MAX_PAGES)

    logger.info("Bulk shares-float loaded: %d symbols with a non-zero float", len(result))
    return result
