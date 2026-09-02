import asyncio
import logging

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.live.stock import StockDataStream
from alpaca.trading.client import TradingClient

from app.core.config import Settings
from app.trading.errors import LiveTradingRefused
from app.trading.errors import LiveTradingRefused
from app.trading.errors import LiveTradingRefused

logger = logging.getLogger(__name__)


class AlpacaClients:
    """Holds the long-lived Alpaca client singletons used across the app.

    One instance is created at startup and stored on app.state so every
    router/service shares the same connections instead of re-authenticating.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.feed = DataFeed(settings.alpaca_data_feed)
        self.options_feed = OptionsFeed(settings.alpaca_options_feed)

        self.trading = TradingClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
            paper=settings.alpaca_paper,
        )
        # The real-money account, only when its own key pair is configured.
        # Nothing else here splits by account: market data, news, the
        # screener, options chains and the stream all stay on the primary
        # (paper) keys, which is also where the data subscription lives.
        self.trading_live: TradingClient | None = (
            TradingClient(
                api_key=settings.alpaca_live_api_key_id,
                secret_key=settings.alpaca_live_api_secret_key,
                paper=False,
            )
            if settings.has_live_credentials
            else None
        )
        self.data = StockHistoricalDataClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        )
        self.news = NewsClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        )
        self.screener = ScreenerClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        )
        self.options = OptionHistoricalDataClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        )
        self.stream = StockDataStream(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
            feed=self.feed,
        )
        self._stream_task: asyncio.Task | None = None

    def trading_for(self, account: str) -> TradingClient:
        """The TradingClient for "paper" or "live" -- see app.trading.guards
        for the gate that decides whether a live client may be *written*
        through; this only answers which one a request is talking about."""
        if account == "paper":
            return self.trading
        if account == "live":
            if self.trading_live is None:
                raise LiveTradingRefused(
                    "No live account configured. Set ALPACA_LIVE_API_KEY_ID and "
                    "ALPACA_LIVE_API_SECRET_KEY in backend/.env."
                )
            return self.trading_live
        raise ValueError(f"Unknown trading account: {account!r}")

    def trading_for(self, account: str) -> TradingClient:
        """The TradingClient for "paper" or "live" -- see app.trading.guards
        for the gate that decides whether a live client may be *written*
        through; this only answers which one a request is talking about."""
        if account == "paper":
            return self.trading
        if account == "live":
            if self.trading_live is None:
                raise LiveTradingRefused(
                    "No live account configured. Set ALPACA_LIVE_API_KEY_ID and "
                    "ALPACA_LIVE_API_SECRET_KEY in backend/.env."
                )
            return self.trading_live
        raise ValueError(f"Unknown trading account: {account!r}")

    def trading_for(self, account: str) -> TradingClient:
        """The TradingClient for "paper" or "live" -- see app.trading.guards
        for the gate that decides whether a live client may be *written*
        through; this only answers which one a request is talking about."""
        if account == "paper":
            return self.trading
        if account == "live":
            if self.trading_live is None:
                raise LiveTradingRefused(
                    "No live account configured. Set ALPACA_LIVE_API_KEY_ID and "
                    "ALPACA_LIVE_API_SECRET_KEY in backend/.env."
                )
            return self.trading_live
        raise ValueError(f"Unknown trading account: {account!r}")

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
