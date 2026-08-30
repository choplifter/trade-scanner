"""Background fill-check loop for Simulation Mode -- the counterpart to
ScannerEngine.run_loop, started the same way in main.py's lifespan.

Batches live prices for every symbol with a working sim order, the same
approach routers/watchlist.py's quotes endpoint uses (one StockSnapshotRequest
per tick, not a subscription per symbol -- StreamManager's live-subscription
cap doesn't apply here), and hands them to SimBroker.check_fills. Skips the
network call entirely when nobody has a working sim order, so Simulation
Mode costs nothing when nobody's using it.
"""

import asyncio
import logging

from alpaca.data.requests import StockSnapshotRequest

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.scanners.formulas import resolve_last_price
from app.trading.sim.broker import SimBroker
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


async def run_sim_fill_loop(clients: AlpacaClients, settings: Settings, store: SimStore) -> None:
    """Runs unconditionally whenever the app is up -- independent of
    trading_enabled/alpaca_paper, since SimBroker never touches the real
    broker client. Idles harmlessly with no Alpaca credentials or no
    working sim orders."""
    broker = SimBroker(store)
    while True:
        try:
            working = await store.working_orders_by_symbol()
            symbols = sorted(working.keys())
            if symbols and settings.has_credentials:
                prices = await _batch_prices(clients, symbols)
                if prices:
                    await broker.check_fills(prices)
        except Exception:
            logger.exception("Sim fill loop tick failed")
        await asyncio.sleep(settings.trading_sim_fill_check_interval)
