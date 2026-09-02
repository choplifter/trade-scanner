"""Batched last prices for the trigger loop. The scanner engine already
holds a live last price for every ranked symbol; anything else is one
StockSnapshotRequest for the whole batch -- the same approach as
app.trading.sim.loop._batch_prices, copied rather than imported so the sim
code stays untouched by the options work."""

import asyncio
import logging

from alpaca.data.requests import StockSnapshotRequest

from app.alpaca.client import AlpacaClients
from app.scanners.formulas import resolve_last_price

logger = logging.getLogger(__name__)


async def batch_last_prices(clients: AlpacaClients, engine, symbols: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    missing: list[str] = []
    rows = getattr(engine, "rows", {}) if engine is not None else {}
    for symbol in symbols:
        row = rows.get(symbol) if rows else None
        last = getattr(row, "last_price", None) if row is not None else None
        if last:
            prices[symbol] = float(last)
        else:
            missing.append(symbol)
    if not missing:
        return prices
    snapshots = await asyncio.to_thread(
        clients.data.get_stock_snapshot,
        StockSnapshotRequest(symbol_or_symbols=missing, feed=clients.feed),
    )
    for symbol, snap in (snapshots or {}).items():
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
