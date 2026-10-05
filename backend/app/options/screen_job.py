"""The screen, run in the background so the tab does not have to wait.

A universe screen is one chain fetch per survivor of stage one: forty
symbols, about a dozen seconds. That is a reasonable thing to ask for and
an unreasonable thing to pay on every tab open, so this loop runs it
during the regular session and stores the answer
(app.options.screen_store). The tab reads the stored run instantly and
can still ask for a live one.

Which strategies: the premium-selling ones, because they are what a
screen run on a schedule is for -- a credit spread that appears at 11:00
is still there at 11:30, while a long option is a view someone forms
rather than a list they watch. Two of them rather than seven, since each
costs its own pass over the universe.

Timing is deliberately unhurried. Every SCREEN_INTERVAL_SECONDS during the
regular session, and never outside it: a screen run at 03:00 prices
yesterday's quotes and would replace a good table with a stale one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.options.screener import ScreenRequest, screen_underlyings
from app.services.market_clock import ET, current_session

logger = logging.getLogger(__name__)

# Half an hour: the criteria a screen reads (open interest, quote widths,
# implied against realised volatility) move on that scale, and a shorter
# cycle would spend the rate limit to redraw the same table.
SCREEN_INTERVAL_SECONDS = 1800.0
# How many symbols each pass prices. Higher than the tab's own 40, because
# nobody is waiting: this is the run that makes the stored table worth
# reading rather than a subset of it.
LIMIT = 60
STRATEGIES = ("credit_spread", "cash_secured_put")


async def record_screen(predictions, strategy: str, rows: list[dict]) -> int:
    """Every row with a valued structure, as a prediction to settle after
    its expiry (app.options.track_record) -- once a day per structure."""
    if predictions is None:
        return 0
    from app.options.track_record import from_screen_row

    made = [p for p in (from_screen_row(row, strategy) for row in rows) if p is not None]
    return await predictions.record(made)


def due(now_et: datetime, last_run: datetime | None, interval: float = SCREEN_INTERVAL_SECONDS) -> bool:
    """Whether a pass is due: inside the regular session, and either the
    first of the session or far enough after the last one."""
    if current_session(now_et) != "regular":
        return False
    if last_run is None:
        return True
    return (now_et - last_run).total_seconds() >= interval


async def run_once(service_factory, clients, store, state, *, limit: int = LIMIT) -> dict[str, int]:
    """One pass per strategy, stored. Returns {strategy: rows} for the log
    -- a pass that priced nothing is worth seeing in the log rather than
    silently replacing yesterday's table with an empty one."""
    counts: dict[str, int] = {}
    for strategy in STRATEGIES:
        service = service_factory()
        if service is None:
            continue
        try:
            body = await screen_underlyings(
                service,
                clients,
                ScreenRequest(symbols=None, scan_universe=True, limit=limit, strategy=strategy),
                earnings_calendar=getattr(state, "earnings_calendar", None),
                iv_store=getattr(state, "iv_history_store", None),
                universe=getattr(state, "universe", None),
            )
        except Exception:
            logger.warning("Background screen failed for %s", strategy, exc_info=True)
            continue
        rows = body.get("rows") or []
        if rows:
            await store.save(strategy, body)
            await record_screen(getattr(state, "prediction_store", None), strategy, rows)
        counts[strategy] = len(rows)
    return counts


async def run_screen_job_loop(
    service_factory,
    clients,
    store,
    state,
    *,
    interval: float = SCREEN_INTERVAL_SECONDS,
    check_interval: float = 300.0,
    now: callable = lambda: datetime.now(timezone.utc).astimezone(ET),
) -> None:
    last_run: datetime | None = None
    while True:
        try:
            moment = now()
            if store is not None and due(moment, last_run, interval):
                counts = await run_once(service_factory, clients, store, state)
                last_run = moment
                if counts:
                    logger.info("Background screen: %s", ", ".join(f"{k} {v} rows" for k, v in counts.items()))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background screen pass failed")
            last_run = now()
        await asyncio.sleep(check_interval)
