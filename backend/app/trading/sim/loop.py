"""Background fill-check loop for Simulation Mode -- the counterpart to
ScannerEngine.run_loop, started the same way in main.py's lifespan.

Batches live prices for every symbol with a working sim order, the same
approach routers/watchlist.py's quotes endpoint uses (one StockSnapshotRequest
per tick, not a subscription per symbol -- StreamManager's live-subscription
cap doesn't apply here), and hands them to SimBroker.check_fills. Skips the
network call entirely when nobody has a working sim order, so Simulation
Mode costs nothing when nobody's using it.

Users in a history replay are left alone here: their clock is the
replay's, and app.replay.loop fills their orders against the replayed
price on every tick (playing or paused -- see routers/trading_sim.py's
_replay_seam). Pricing them here as well would fill a replayed order at
today's price.

The simulated options book rides the same tick: resting packages are
priced against live snapshots, contracts past their expiry are settled,
and the book's own exit triggers are checked -- see
app.trading.sim.options_book and app.options.sim_monitor.
"""

import asyncio
import logging
from datetime import datetime

from alpaca.data.requests import StockSnapshotRequest

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.options.sim_monitor import check_sim_triggers
from app.scanners.formulas import resolve_last_price
from app.services.market_clock import ET, current_session
from app.trading.sim.broker import SimBroker
from app.trading.sim.options_service import OptionsWiring, make_sim_options_service
from app.trading.sim.store import SimStore

logger = logging.getLogger(__name__)


async def _batch_prices(clients: AlpacaClients, symbols: list[str]) -> dict[str, float]:
    snapshots = await asyncio.to_thread(
        clients.data.get_stock_snapshot,
        StockSnapshotRequest(symbol_or_symbols=symbols, feed=clients.feed),
    )
    prices: dict[str, float] = {}
    for symbol, snap in snapshots.items():
        if snap is None:
            continue
        daily_bar = snap.daily_bar
        last = resolve_last_price(
            snap.latest_trade.price if snap.latest_trade else None,
            daily_bar.close if daily_bar else None,
            snap.previous_daily_bar.close if snap.previous_daily_bar else None,
            daily_bar.low if daily_bar else None,
            daily_bar.high if daily_bar else None,
        )
        if last is not None:
            prices[symbol] = last
    return prices


def symbols_to_price(by_user: dict[int, dict[str, list[dict]]], replaying: set[int]) -> list[str]:
    """The union of symbols with a working stock order, leaving out the
    users whose clock is a replay's."""
    return sorted(
        {
            symbol
            for user_id, orders_by_symbol in by_user.items()
            if user_id not in replaying
            for symbol in orders_by_symbol
        }
    )


async def _tick_options(
    clients: AlpacaClients, settings: Settings, store: SimStore, wiring: OptionsWiring, replaying: set[int]
) -> None:
    """One pass over every user with a resting package, an open contract
    or an armed sim trigger, at live prices."""
    users = await wiring.options_store.users_with_work()
    users |= {int(t["user_id"]) for t in await wiring.trigger_store.all_active("sim")}
    users -= replaying
    if not users:
        return
    regular = current_session() == "regular"
    now = datetime.now(ET)
    for user_id in sorted(users):
        try:
            service = make_sim_options_service(
                clients,
                settings,
                sim_store=store,
                options_store=wiring.options_store,
                user_id=user_id,
                seam=None,
                option_engines=None,
                chain_cache=wiring.chain_cache,
                engine=wiring.scanner_engine,
            )
            if regular:
                await service.book.check_fills(service.source, now)
            await service.book.settle_expired(service.source, now)
            if regular:
                await check_sim_triggers(user_id, service, wiring.trigger_store, settings)
        except Exception:
            logger.exception("Sim options tick failed for user %s", user_id)


async def run_sim_fill_loop(
    clients: AlpacaClients,
    settings: Settings,
    store: SimStore,
    *,
    replay_store=None,
    wiring: OptionsWiring | None = None,
) -> None:
    """Runs unconditionally whenever the app is up -- independent of
    trading_enabled/alpaca_paper, since SimBroker never touches the real
    broker client. Idles harmlessly with no Alpaca credentials or no
    working sim orders.

    Serves every user off one shared batched price fetch: the union of
    symbols across everyone's working orders is priced in a single
    StockSnapshotRequest, then each user's own SimBroker checks fills
    against that same price map -- one broker instance per user (a
    SimBroker is cheap, just a store reference plus a user_id) rather than
    one per-user network round trip.
    """
    while True:
        try:
            replaying: set[int] = set()
            if replay_store is not None:
                replaying = {int(s["user_id"]) for s in await replay_store.all_sessions()}
            by_user = await store.all_working_orders()
            symbols = symbols_to_price(by_user, replaying)
            if symbols and settings.has_credentials:
                prices = await _batch_prices(clients, symbols)
                if prices:
                    for user_id in by_user:
                        if user_id in replaying:
                            continue
                        await SimBroker(store, user_id).check_fills(prices)
            if wiring is not None and settings.has_credentials:
                await _tick_options(clients, settings, store, wiring, replaying)
        except Exception:
            logger.exception("Sim fill loop tick failed")
        await asyncio.sleep(settings.trading_sim_fill_check_interval)
