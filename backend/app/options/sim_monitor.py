"""The simulated book's exit triggers: the same check_one the live loop
runs (app.options.monitor), against the book's own prices -- the replay
moment in a replay, live snapshots otherwise -- and the book's own
positions. Called per user from whichever loop owns that user's clock
(app.replay.loop while replaying, app.trading.sim.loop live), never from
the live trigger loop, which skips account "sim".
"""

import logging

from app.core.config import Settings
from app.options.monitor import check_one, premium_of, wants_premium
from app.options.trigger_store import TriggerStore

logger = logging.getLogger(__name__)

SIM_ACCOUNT = "sim"


async def check_sim_triggers(user_id: int, service, trigger_store: TriggerStore, settings: Settings) -> None:
    """`service` is the user's SimOptionsService (spot, source.leg_quotes,
    spreads, close_spread at the book's moment)."""
    triggers = [t for t in await trigger_store.all_active(SIM_ACCOUNT) if int(t.get("user_id") or 0) == user_id]
    if not triggers:
        return
    symbols = sorted({leg["symbol"] for t in triggers if wants_premium(t) for leg in t.get("legs") or []})
    quotes: dict = {}
    if symbols:
        try:
            quotes = await service.source.leg_quotes(symbols)
        except Exception:
            logger.exception("Sim premium trigger quote fetch failed for user %s", user_id)
    spots: dict[str, float | None] = {}
    for trigger in triggers:
        underlying = trigger["underlying"]
        if underlying not in spots:
            try:
                spots[underlying] = await service.spot(underlying)
            except Exception:
                logger.exception("Sim trigger spot lookup failed for %s", underlying)
                spots[underlying] = None
        premium = premium_of(trigger, quotes) if wants_premium(trigger) and quotes else None
        await check_one(service, trigger_store, settings, trigger, spots[underlying], premium)
