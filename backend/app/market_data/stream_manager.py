import asyncio
import logging

from app.alpaca.client import AlpacaClients
from app.market_data.vwap import SessionVwapState
from app.ws.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

# Trade conditions the SIP excludes from a bar's open/high/low/close (they
# still count toward volume): average-price, derivatively-priced, cash and
# next-day sales, odd lots, prior-reference and out-of-sequence prints, and
# so on. Alpaca applies the same list when it builds the minute bars we
# already stream, so filtering here keeps the tick-shaped forming candle
# consistent with the closed bar that later replaces it -- an odd-lot print
# a dollar off the market must not spike a wick that the real bar then
# erases. Form T ("T", extended hours) is deliberately not in the set: it is
# the *only* kind of print outside 09:30-16:00, and Alpaca's premarket and
# after-hours bars do carry prices, so those trades must shape the candle.
# List per Alpaca's minute-bar write-up (alpaca.markets/learn/stock-minute-bars).
TRADE_PRICE_EXCLUDED_CONDITIONS = frozenset("B W 4 7 9 C G H I M N P Q R U V Z".split())

# How long trades for one symbol accumulate before the batch goes out to the
# chart sockets. SPY alone prints thousands of times a second on the
# consolidated tape; forwarding each one would swamp the socket and the
# browser's render loop for no visible gain. Four updates a second is
# TradingView-like motion on the forming candle.
TRADE_FLUSH_INTERVAL_SECONDS = 0.25


class StreamManager:
    """Ref-counted wrapper around the single shared StockDataStream so two
    chart widgets open on the same symbol share one live subscription, and a
    symbol is only unsubscribed once nothing references it anymore.
    """

    def __init__(self, clients: AlpacaClients, manager: ConnectionManager, max_symbols: int):
        self.clients = clients
        self.manager = manager
        self.max_symbols = max_symbols
        self._ref_counts: dict[str, int] = {}
        self._vwap_states: dict[str, SessionVwapState] = {}
        # Per-symbol OHLCV of the trades seen since the last flush, keyed by
        # symbol; the flush task that will drain it lives alongside so a
        # second trade in the same window doesn't schedule a second flush.
        self._pending_trades: dict[str, dict] = {}
        self._flush_tasks: dict[str, asyncio.Task] = {}

    def get_or_create_vwap_state(self, symbol: str) -> SessionVwapState:
        return self._vwap_states.setdefault(symbol, SessionVwapState(symbol=symbol))

    async def subscribe(self, symbol: str) -> None:
        symbol = symbol.upper()
        count = self._ref_counts.get(symbol, 0)
        if count > 0:
            self._ref_counts[symbol] = count + 1
            return
        if len(self._ref_counts) >= self.max_symbols:
            raise ValueError(
                f"Live symbol cap reached ({self.max_symbols}). Close another chart widget first."
            )
        # Lazily start the websocket connection on the first-ever chart
        # subscription rather than at app startup, so an idle dashboard with
        # no open chart widgets doesn't hold a live connection for nothing.
        self.clients.start_stream()
        self._ref_counts[symbol] = 1
        self.get_or_create_vwap_state(symbol)
        # alpaca-py's subscribe_bars/unsubscribe_bars internally do
        # asyncio.run_coroutine_threadsafe(..., stream_loop).result() with no
        # timeout. Our stream runs as a task on *this same* FastAPI event
        # loop (see AlpacaClients.start_stream), so calling these directly
        # from a coroutine on that loop deadlocks it permanently: the call
        # blocks the only thread that could ever run the scheduled coroutine
        # and complete the future it's waiting on. Running it in a worker
        # thread via to_thread keeps the blocking wait off the loop thread.
        await asyncio.to_thread(self.clients.stream.subscribe_bars, self._on_bar, symbol)
        # Trades ride along with the bars: the bar is the authoritative
        # closed candle once a minute, the trades shape the candle that is
        # still forming in between (see _on_trade).
        await asyncio.to_thread(self.clients.stream.subscribe_trades, self._on_trade, symbol)
        logger.info("Subscribed live bars and trades for %s", symbol)

    async def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.upper()
        count = self._ref_counts.get(symbol, 0)
        if count <= 1:
            self._ref_counts.pop(symbol, None)
            self._vwap_states.pop(symbol, None)
            await asyncio.to_thread(self.clients.stream.unsubscribe_bars, symbol)
            await asyncio.to_thread(self.clients.stream.unsubscribe_trades, symbol)
            self._pending_trades.pop(symbol, None)
            flush_task = self._flush_tasks.pop(symbol, None)
            if flush_task is not None:
                flush_task.cancel()
            logger.info("Unsubscribed live bars and trades for %s", symbol)
        else:
            self._ref_counts[symbol] = count - 1

    async def _on_bar(self, bar) -> None:
        vwap_state = self.get_or_create_vwap_state(bar.symbol)
        vwap = vwap_state.update(
            timestamp=bar.timestamp,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            bar_vwap=getattr(bar, "vwap", None),
        )
        await self.manager.broadcast(
            f"chart:{bar.symbol}",
            {
                "type": "bar",
                "symbol": bar.symbol,
                "bar": {
                    "t": bar.timestamp.isoformat(),
                    "o": bar.open,
                    "h": bar.high,
                    "l": bar.low,
                    "c": bar.close,
                    "v": bar.volume,
                },
                "vwap": vwap,
                # The premarket-anchored line, shipped alongside rather than
                # instead: the chart toggles between them client-side, which
                # is the only way one shared accumulator per symbol can serve
                # viewers who have chosen different anchors.
                "vwap_premarket": vwap_state.premarket_anchored_vwap,
            },
        )

    async def _on_trade(self, trade) -> None:
        """Fold one print into the symbol's pending batch and make sure a
        flush is scheduled. Price-excluded conditions (odd lots, out-of-
        sequence prints, ...) add volume only, matching how the SIP and
        Alpaca's own bars treat them.
        """
        conditions = getattr(trade, "conditions", None) or ()
        if isinstance(conditions, str):
            conditions = (conditions,)
        price_eligible = not any(c in TRADE_PRICE_EXCLUDED_CONDITIONS for c in conditions)

        pending = self._pending_trades.get(trade.symbol)
        if pending is None:
            pending = {"o": None, "h": None, "l": None, "c": None, "v": 0.0, "t": None}
            self._pending_trades[trade.symbol] = pending
        pending["v"] += float(trade.size)
        if price_eligible:
            price = float(trade.price)
            if pending["o"] is None:
                pending["o"] = price
                pending["h"] = price
                pending["l"] = price
            else:
                pending["h"] = max(pending["h"], price)
                pending["l"] = min(pending["l"], price)
            pending["c"] = price
            # The minute this print belongs to -- the same bucket key the
            # closed bar for that minute will carry, so the chart can tell
            # "still the forming candle" from "a new minute has started".
            pending["t"] = trade.timestamp.replace(second=0, microsecond=0)

        if trade.symbol not in self._flush_tasks:
            self._flush_tasks[trade.symbol] = asyncio.create_task(self._flush_trades(trade.symbol))

    async def _flush_trades(self, symbol: str) -> None:
        try:
            await asyncio.sleep(TRADE_FLUSH_INTERVAL_SECONDS)
        finally:
            self._flush_tasks.pop(symbol, None)
        pending = self._pending_trades.pop(symbol, None)
        # Nothing price-eligible in the window (only odd lots, say): there is
        # no candle shape to send, and volume alone is corrected by the next
        # closed bar anyway.
        if pending is None or pending["o"] is None:
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
