"""Background pacing loop for history replay -- the counterpart to
app.trading.sim.loop.run_sim_fill_loop and ScannerEngine.run_loop, started
the same way in main.py's lifespan.

Advances every playing user's clock by one 5-minute bar at a time,
speed-adjusted (settings.replay_bar_seconds / session["speed"] real seconds
per bar), rather than trying to divide a fixed tick into fractional bar
advances -- simpler, and bars are the only granularity ReplayEngine can
answer at anyway. Per-user due times are tracked in memory only
(_next_due); losing that on restart just means the next tick advances
immediately, which is harmless.

Each advance re-broadcasts that user's three ranked views over their own
WS topic (topic includes user_id -- unlike the live scanner's single
shared broadcast, replay is per-user) and runs a replay-aware fill check
for that user's working sim orders, reusing SimBroker.check_fills exactly
as the live fill loop does, just sourced from ReplayEngine.reference_price
instead of a live snapshot.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.replay.engine import ReplayEngineCache, load_replay_engine
from app.replay.store import ReplayStore
from app.trading.sim.broker import SimBroker
from app.trading.sim.store import SimStore
from app.ws.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

_BAR_STEP = timedelta(minutes=5)


def topic_for(user_id: int, scanner: str) -> str:
    """Public: also used by ws.replay_ws to subscribe/reply on the same
    topic this loop broadcasts to."""
    return f"replay:{user_id}:{scanner}"


def replay_update_payload(scanner: str, as_of, rows) -> dict:
    return {
        "type": "replay_update",
        "scanner": scanner,
        "as_of": as_of.isoformat(),
        "rows": [r.model_dump(mode="json") for r in rows],
    }


async def _advance_one(
    user_id: int,
    session: dict,
    replay_store: ReplayStore,
    sim_store: SimStore,
    engines: ReplayEngineCache,
    manager: ConnectionManager,
    clients: AlpacaClients,
    settings: Settings,
) -> None:
    engine = engines.get(user_id)
    if engine is None:
        # Resident cache lost (server restart, or this session was started
        # by a prior process) -- rebuild from the persisted session row
        # rather than failing the tick silently.
        engine = await load_replay_engine(clients, session["symbols"], session["lookback_days"])
        engines.put(user_id, engine)

    as_of = datetime.fromisoformat(session["as_of"]) + _BAR_STEP
    if engine.end is not None and as_of > engine.end:
        # Ran off the end of the fetched range -- stop rather than freeze
        # silently on the last bar, so the UI can tell the difference.
        await replay_store.update(user_id, playing=0)
        return

    await replay_store.update(user_id, as_of=as_of.isoformat())

    views = engine.snapshot_views(as_of, settings.scanner_min_dollar_volume)
    for scanner, rows in views.items():
        topic = topic_for(user_id, scanner)
        if manager.has_subscribers(topic):
            await manager.broadcast(topic, replay_update_payload(scanner, as_of, rows))

    working = await sim_store.working_orders_by_symbol(user_id)
    if working:
        prices = {
            symbol: price
            for symbol in working
            if (price := engine.reference_price(symbol, as_of)) is not None
        }
        if prices:
            await SimBroker(sim_store, user_id).check_fills(prices)


async def run_replay_pacing_loop(
    replay_store: ReplayStore,
    sim_store: SimStore,
    engines: ReplayEngineCache,
    manager: ConnectionManager,
    clients: AlpacaClients,
    settings: Settings,
) -> None:
    next_due: dict[int, float] = {}
    while True:
        try:
            sessions = await replay_store.all_playing()
            playing_ids = {s["user_id"] for s in sessions}
            for user_id in list(next_due):
                if user_id not in playing_ids:
                    del next_due[user_id]

            now = time.monotonic()
            for session in sessions:
                user_id = session["user_id"]
                if now < next_due.get(user_id, 0.0):
                    continue
                try:
                    await _advance_one(
                        user_id, session, replay_store, sim_store, engines, manager, clients, settings
                    )
                except Exception:
                    logger.exception("Replay pacing tick failed for user %s", user_id)
                speed = session["speed"] or 1.0
                next_due[user_id] = now + max(settings.replay_bar_seconds / speed, settings.replay_pacing_check_interval)
        except Exception:
            logger.exception("Replay pacing loop tick failed")
        await asyncio.sleep(settings.replay_pacing_check_interval)
