import asyncio
import logging

from alpaca.data.enums import DataFeed
from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.live.stock import StockDataStream
from alpaca.trading.client import TradingClient

from app.core.config import Settings

logger = logging.getLogger(__name__)


class AlpacaClients:
    """Holds the long-lived Alpaca client singletons used across the app.

    One instance is created at startup and stored on app.state so every
    router/service shares the same connections instead of re-authenticating.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.feed = DataFeed(settings.alpaca_data_feed)

        self.trading = TradingClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
            paper=settings.alpaca_paper,
        )
        self.data = StockHistoricalDataClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        )
        self.news = NewsClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        )
        self.stream = StockDataStream(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
            feed=self.feed,
        )
        self._stream_task: asyncio.Task | None = None

    def start_stream(self) -> None:
        """Run the live websocket stream on our existing asyncio loop.

        StockDataStream.run() is a blocking call that spins up its own
        asyncio.run() loop, which is incompatible with running inside FastAPI's
        already-running loop. _run_forever() is the coroutine run() wraps, so
        we schedule it directly as a background task instead. subscribe_bars/
        unsubscribe_bars remain plain sync calls made from request handlers.
        """
        if self._stream_task is not None:
            return
        self._stream_task = asyncio.create_task(self.stream._run_forever())
        logger.info("Alpaca stream task started (feed=%s)", self.feed)

    async def stop_stream(self) -> None:
        if self._stream_task is None:
            return
        await self.stream.stop_ws()
        self._stream_task.cancel()
        self._stream_task = None
