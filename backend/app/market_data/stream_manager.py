import asyncio
import logging

from app.alpaca.client import AlpacaClients
from app.market_data.vwap import SessionVwapState
from app.ws.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


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
        logger.info("Subscribed live bars for %s", symbol)

    async def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.upper()
        count = self._ref_counts.get(symbol, 0)
        if count <= 1:
            self._ref_counts.pop(symbol, None)
            self._vwap_states.pop(symbol, None)
            await asyncio.to_thread(self.clients.stream.unsubscribe_bars, symbol)
            logger.info("Unsubscribed live bars for %s", symbol)
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
