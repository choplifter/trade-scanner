"""Live option-contract data for the premium chart: the OptionDataStream's
trades and quotes, fanned out over the same chart:{symbol} topic the stock
stream uses, in the same message shapes -- so the chart's forming-candle
logic serves both.

Mirrors app.market_data.stream_manager.StreamManager (ref-counted
subscriptions, a lazily started stream, batched trade flushes) but is its
own class rather than a subclass: options carry no VWAP, no closed-bar
stream (the chart's REST poll supplies those) and different trade
condition codes, so the parts that would be shared are the few lines that
aren't the interesting ones.
"""

import asyncio
import logging

from app.alpaca.client import AlpacaClients
from app.ws.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

# Same cadence as the stock stream's trade batches: four updates a second
# is TradingView-like motion on the forming candle.
TRADE_FLUSH_INTERVAL_SECONDS = 0.25
# Quotes update far more often than trades on an active contract; one
# bid/ask per symbol per window is plenty for a header readout.
QUOTE_FLUSH_INTERVAL_SECONDS = 0.5


class OptionStreamManager:
    def __init__(self, clients: AlpacaClients, manager: ConnectionManager, max_symbols: int):
        self.clients = clients
        self.manager = manager
        self.max_symbols = max_symbols
        self._ref_counts: dict[str, int] = {}
        self._pending_trades: dict[str, dict] = {}
        self._flush_tasks: dict[str, asyncio.Task] = {}
        self._pending_quotes: dict[str, dict] = {}
        self._quote_tasks: dict[str, asyncio.Task] = {}

    async def subscribe(self, symbol: str) -> None:
        symbol = symbol.upper()
        count = self._ref_counts.get(symbol, 0)
        if count > 0:
            self._ref_counts[symbol] = count + 1
            return
        if len(self._ref_counts) >= self.max_symbols:
            raise ValueError(
                f"Live contract cap reached ({self.max_symbols}). Close another premium chart first."
            )
        self.clients.start_option_stream()
        self._ref_counts[symbol] = 1
        # to_thread for the same reason as StreamManager.subscribe: the SDK's
        # subscribe_* block on a future scheduled onto this very loop.
        await asyncio.to_thread(self.clients.option_stream.subscribe_trades, self._on_trade, symbol)
        await asyncio.to_thread(self.clients.option_stream.subscribe_quotes, self._on_quote, symbol)
        logger.info("Subscribed live option trades and quotes for %s", symbol)

    async def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.upper()
        count = self._ref_counts.get(symbol, 0)
        if count <= 1:
            if symbol not in self._ref_counts:
                return
            self._ref_counts.pop(symbol, None)
            await asyncio.to_thread(self.clients.option_stream.unsubscribe_trades, symbol)
            await asyncio.to_thread(self.clients.option_stream.unsubscribe_quotes, symbol)
            self._pending_trades.pop(symbol, None)
            self._pending_quotes.pop(symbol, None)
            for tasks in (self._flush_tasks, self._quote_tasks):
                task = tasks.pop(symbol, None)
                if task is not None:
                    task.cancel()
            logger.info("Unsubscribed live option trades and quotes for %s", symbol)
        else:
            self._ref_counts[symbol] = count - 1

    async def _on_trade(self, trade) -> None:
        """Fold one print into the contract's pending batch. Every print
        shapes the candle: OPRA's condition codes do not carry the SIP's
        "excluded from OHLC" semantics, and Alpaca's option bars count all
        of them."""
        pending = self._pending_trades.get(trade.symbol)
        price = float(trade.price)
        if pending is None:
            pending = {"o": price, "h": price, "l": price, "c": price, "v": 0.0, "t": None}
            self._pending_trades[trade.symbol] = pending
        pending["v"] += float(trade.size)
        pending["h"] = max(pending["h"], price)
        pending["l"] = min(pending["l"], price)
        pending["c"] = price
        pending["t"] = trade.timestamp.replace(second=0, microsecond=0)
        if trade.symbol not in self._flush_tasks:
            self._flush_tasks[trade.symbol] = asyncio.create_task(self._flush_trades(trade.symbol))

    async def _flush_trades(self, symbol: str) -> None:
        try:
            await asyncio.sleep(TRADE_FLUSH_INTERVAL_SECONDS)
        finally:
            self._flush_tasks.pop(symbol, None)
        pending = self._pending_trades.pop(symbol, None)
        if pending is None or pending["t"] is None:
            return
        await self.manager.broadcast(
            f"chart:{symbol}",
            {
                "type": "trade",
                "symbol": symbol,
                "t": pending["t"].isoformat(),
                "o": pending["o"],
                "h": pending["h"],
                "l": pending["l"],
                "c": pending["c"],
                "v": pending["v"],
            },
        )

    async def _on_quote(self, quote) -> None:
        """Keep only the newest quote per window; the flush sends it."""
        self._pending_quotes[quote.symbol] = {
            "t": quote.timestamp.isoformat(),
            "bid": float(quote.bid_price) if quote.bid_price is not None else None,
            "ask": float(quote.ask_price) if quote.ask_price is not None else None,
            "bid_size": float(quote.bid_size) if quote.bid_size is not None else None,
            "ask_size": float(quote.ask_size) if quote.ask_size is not None else None,
        }
        if quote.symbol not in self._quote_tasks:
            self._quote_tasks[quote.symbol] = asyncio.create_task(self._flush_quote(quote.symbol))

    async def _flush_quote(self, symbol: str) -> None:
        try:
            await asyncio.sleep(QUOTE_FLUSH_INTERVAL_SECONDS)
        finally:
            self._quote_tasks.pop(symbol, None)
        pending = self._pending_quotes.pop(symbol, None)
        if pending is None:
            return
        await self.manager.broadcast(f"chart:{symbol}", {"type": "quote", "symbol": symbol, **pending})
