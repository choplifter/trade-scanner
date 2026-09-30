"""One at-the-money implied volatility per liquid symbol per session, so
that an IV rank can exist at all.

The store it writes to (app.options.iv_history_store) fills opportunistically
-- a reading is recorded whenever someone happens to open a chain -- which
means the rank exists only for symbols already being watched, and only once
twenty sessions of watching have passed. Measured on this deployment: fifty
symbols carried a reading, exactly one of them (SPY) had enough of them.

A screen that wants "IV percentile above 50" across the market therefore
needs the history to be *collected*, not stumbled upon. This loop does one
pass a session over the names a screen would consider anyway (the
Screener's own stage one, so the two agree on what "liquid" means) and
records their ATM IV.

Deliberately once a session, not on a timer: an IV rank compares today's
reading with past ones, and readings taken at different times of day are
not comparable. The pass runs once the market has been open for
OPEN_SETTLE_MINUTES -- long enough that the opening auction's noise has
cleared, early enough that a short session still gets its row.

Cost: one chain fetch per symbol, once a day, at a moment nothing else is
competing for the rate limit. Nothing here blocks a request; a failure is
logged and the symbol simply has no reading for the day, which the rank
already knows how to report.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from app.options.chain import Chain
from app.options.screener import MIN_DOLLAR_VOLUME, atm_iv_of, pick_expiry
from app.services.market_clock import ET, current_session

logger = logging.getLogger(__name__)

# How long after the open the pass runs. The first half hour prices the
# overnight gap rather than the day, and its IV is not comparable with a
# reading taken mid-session.
OPEN_SETTLE_MINUTES = 45
# The expiry whose at-the-money IV is recorded. A rank compares a symbol
# with its own past, so the *same* part of the curve has to be read every
# time; 30-60 days is the stretch a screen trades and the one least
# distorted by a single earnings date.
DTE_RANGE = (30, 60)
# How many symbols carry a history. Each is a chain fetch once a day, so
# this is a rate-limit budget, not a view on breadth.
MAX_SYMBOLS = 150
# How often the loop wakes to ask whether today's pass is due.
CHECK_INTERVAL_SECONDS = 300.0


def symbols_to_record(universe: dict, watchlist: list[str] | None = None, limit: int = MAX_SYMBOLS) -> list[str]:
    """The liquid names, plus whatever the reader keeps on their watchlist.

    The watchlist goes in first and unconditionally: those are the symbols
    someone will actually look up a rank for, and a thin one they follow
    deliberately is worth a row more than the 150th most liquid name they
    have never opened.
    """
    out: list[str] = []
    seen: set[str] = set()
    for symbol in watchlist or []:
        upper = symbol.upper()
        if upper not in seen:
            seen.add(upper)
            out.append(upper)
    ranked = sorted(
        (
            (float(getattr(entry, "avg_dollar_vol_20d", 0.0) or 0.0), symbol.upper())
            for symbol, entry in (universe or {}).items()
            if float(getattr(entry, "avg_dollar_vol_20d", 0.0) or 0.0) >= MIN_DOLLAR_VOLUME
            and float(getattr(entry, "prev_close", 0.0) or 0.0) > 0
        ),
        reverse=True,
    )
    for _vol, symbol in ranked:
        if len(out) >= limit:
            break
        if symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out[:limit]


def due(now_et: datetime, recorded_on: date | None) -> bool:
    """Whether today's pass should run: a regular session, far enough past
    the open, and nothing written for this date yet."""
    if recorded_on == now_et.date():
        return False
    if current_session(now_et) != "regular":
        return False
    open_at = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    return now_et >= open_at + timedelta(minutes=OPEN_SETTLE_MINUTES)


async def record_once(service, iv_store, symbols: list[str], today: date) -> tuple[int, int]:
    """(recorded, attempted). Sequential rather than gathered: this is a
    background pass with all session to finish, and firing 150 chain
    fetches at once is exactly the burst the rate limit exists for."""
    recorded = 0
    for symbol in symbols:
        try:
            strip = await service.expiries(symbol)
            listed = [date.fromisoformat(e["expiry"]) for e in strip.get("expiries", [])]
            expiry = pick_expiry(listed, today, *DTE_RANGE)
            if expiry is None:
                continue
            chain: Chain = await service.chain(symbol, expiry)
            iv = atm_iv_of(chain.rows, chain.spot)
            if iv is None or iv <= 0:
                continue
            await iv_store.record(symbol, today, iv, (expiry - today).days)
            recorded += 1
        except Exception:
            logger.debug("IV recorder: nothing for %s", symbol, exc_info=True)
    return recorded, len(symbols)


async def run_iv_recorder_loop(
    service_factory,
    iv_store,
    state,
    *,
    interval: float = CHECK_INTERVAL_SECONDS,
    now: callable = lambda: datetime.now(timezone.utc).astimezone(ET),
) -> None:
    """Once a session, record the liquid names' ATM IV.

    `service_factory` returns an OptionsService (the operator's own, since
    market data has no per-account split); `state` is app.state, read at
    run time so a universe that fills after startup is still seen.
    """
    recorded_on: date | None = None
    while True:
        try:
            moment = now()
            if due(moment, recorded_on):
                service = service_factory()
                if service is not None and iv_store is not None:
                    watchlist_store = getattr(state, "watchlist_store", None)
                    watchlist: list[str] = []
                    if watchlist_store is not None:
                        try:
                            watchlist = await watchlist_store.all_symbols()
                        except Exception:
                            logger.debug("IV recorder: no watchlist to read", exc_info=True)
                    symbols = symbols_to_record(getattr(state, "universe", None) or {}, watchlist)
                    done, attempted = await record_once(service, iv_store, symbols, moment.date())
                    logger.info("IV recorder: %d of %d symbols recorded for %s", done, attempted, moment.date())
                recorded_on = moment.date()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("IV recorder pass failed")
            recorded_on = now().date()  # do not retry it all session
        await asyncio.sleep(interval)
