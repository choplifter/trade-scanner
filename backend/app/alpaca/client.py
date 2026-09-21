import asyncio
import logging

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.live.option import OptionDataStream
from alpaca.data.live.stock import StockDataStream
from alpaca.trading.client import TradingClient
from requests.adapters import HTTPAdapter

from app.core.config import Settings
from app.trading.errors import LiveTradingRefused

logger = logging.getLogger(__name__)

# (connect, read) seconds for every Alpaca REST call. alpaca-py sends them
# through a bare requests.Session with no timeout at all, so a connection
# that dies mid-response -- a network drop, a laptop waking up -- leaves the
# thread blocked in recv() forever, and whatever awaits that thread with it.
# On 2026-09-21 that was three option-chain fetches, which froze the scanner
# engine's loop (and with it the market-conditions readout) for two hours.
# The read timeout bounds the silence between two reads, not the whole
# response, so a large paginated chain still arrives; only a dead one stops.
REST_TIMEOUT = (10.0, 30.0)


class _TimeoutAdapter(HTTPAdapter):
    """Fills in a timeout for any request that did not set its own --
    alpaca-py never does."""

    def __init__(self, timeout: tuple[float, float]) -> None:
        self._timeout = timeout
        super().__init__()

    def send(self, request, timeout=None, **kwargs):
        return super().send(request, timeout=timeout if timeout is not None else self._timeout, **kwargs)


def with_timeout(client, timeout: tuple[float, float] = REST_TIMEOUT):
    """Mount the timeout on an alpaca-py REST client. Reaches into its
    private _session because the SDK offers no timeout of its own;
    tests/test_alpaca_timeout.py fails loudly if an upgrade renames it."""
    adapter = _TimeoutAdapter(timeout)
    client._session.mount("https://", adapter)
    client._session.mount("http://", adapter)
    return client


class AlpacaClients:
    """Holds the long-lived Alpaca client singletons used across the app.

    One instance is created at startup and stored on app.state so every
    router/service shares the same connections instead of re-authenticating.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.feed = DataFeed(settings.alpaca_data_feed)
        self.options_feed = OptionsFeed(settings.alpaca_options_feed)

        self.trading = with_timeout(TradingClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
            paper=settings.alpaca_paper,
        ))
        # The real-money account, only when its own key pair is configured.
        # Nothing else here splits by account: market data, news, the
        # screener, options chains and the stream all stay on the primary
        # (paper) keys, which is also where the data subscription lives.
        self.trading_live: TradingClient | None = (
            with_timeout(TradingClient(
                api_key=settings.alpaca_live_api_key_id,
                secret_key=settings.alpaca_live_api_secret_key,
                paper=False,
            ))
            if settings.has_live_credentials
            else None
        )
        self.data = with_timeout(StockHistoricalDataClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        ))
        self.news = with_timeout(NewsClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        ))
        self.screener = with_timeout(ScreenerClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        ))
        self.options = with_timeout(OptionHistoricalDataClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        ))
        self.stream = StockDataStream(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
            feed=self.feed,
        )
        self._stream_task: asyncio.Task | None = None
        # Live option trades/quotes for the premium chart (see
        # app.market_data.option_stream_manager). Its own websocket: Alpaca
        # serves options on a separate endpoint from stocks.
        self.option_stream = OptionDataStream(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
            feed=self.options_feed,
        )
        self._option_stream_task: asyncio.Task | None = None

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

    def start_option_stream(self) -> None:
        """Same shape as start_stream, for the option websocket."""
        if self._option_stream_task is not None:
            return
        self._option_stream_task = asyncio.create_task(self.option_stream._run_forever())
        logger.info("Alpaca option stream task started (feed=%s)", self.options_feed)

    async def stop_option_stream(self) -> None:
        if self._option_stream_task is None:
            return
        await self.option_stream.stop_ws()
        self._option_stream_task.cancel()
        self._option_stream_task = None
