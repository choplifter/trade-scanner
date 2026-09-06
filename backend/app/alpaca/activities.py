"""Alpaca's account activities for options: assignments (OPASN), expirations
(OPEXP) and exercises (OPEXC). None of these is an order, so nothing else
in the app sees them: a short put assigned over the weekend simply becomes
100 shares in the positions list, and the put's round trip never closes in
the journal. This is the one place that asks.

The alpaca-py TradingClient in use has no method for the endpoint (only
the Broker API client does), so this goes through the client's own REST
layer -- same base URL, same keys, same retries -- at
GET /v2/account/activities.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

OPTION_ACTIVITY_TYPES = ("OPASN", "OPEXP", "OPEXC")
PAGE_SIZE = 100


def _plain_activity(row) -> dict:
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "model_dump"):
        return row.model_dump(mode="json")
    return dict(getattr(row, "__dict__", {}))


async def fetch_option_activities(trading_client, *, after: str | None = None, page_size: int = PAGE_SIZE) -> list[dict]:
    """Assignments, expirations and exercises on the account, oldest first,
    from `after` (an ISO timestamp) on. Each row as a plain dict with at
    least id, activity_type, date, symbol, qty. An empty list on failure --
    logged, because a silent empty answer here reads as "nothing happened"."""
    params: dict = {
        "activity_types": ",".join(OPTION_ACTIVITY_TYPES),
        "direction": "asc",
        "page_size": page_size,
    }
    if after:
        params["after"] = after
    try:
        rows = await asyncio.to_thread(trading_client.get, "/account/activities", params)
    except Exception:
        logger.exception("Alpaca account activities fetch failed")
        return []
    if not isinstance(rows, list):
        return []
    return [_plain_activity(r) for r in rows]
