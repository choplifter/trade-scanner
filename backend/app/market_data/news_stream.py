"""Alpaca's news websocket, wildcard-subscribed: every article the moment
it is published, market-wide, into the NewsFeedTracker and out over the
"news:feed" WS topic -- the push half of the live news feed. The
once-a-minute poll in ScannerEngine._refresh_news_feed stays as the
backstop (startup seed, gaps while this reconnects); the tracker's
per-article dedup keeps the two from doubling an entry.

Its own websocket and its own task, like the stock and option streams in
app.alpaca.client, but owned here: nothing else subscribes to news, so
there is no ref counting -- it runs from startup to shutdown and
reconnects itself with a growing pause after any failure.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.core.config import Settings
from app.market_data.news_feed import NewsFeedTracker
from app.ws.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

TOPIC = "news:feed"
_RECONNECT_MIN_SECONDS = 5.0
_RECONNECT_MAX_SECONDS = 60.0


class NewsStreamManager:
    def __init__(self, settings: Settings, tracker: NewsFeedTracker, manager: ConnectionManager) -> None:
        self._settings = settings
        self._tracker = tracker
        self._manager = manager
        self._task: asyncio.Task | None = None
        self._stream = None
        self.connected = False
        self.received = 0

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())
        logger.info("Alpaca news stream task started (market-wide)")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        self._task = None
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                await stream.stop_ws()
            except Exception:
                logger.debug("News stream stop failed", exc_info=True)

    async def _run(self) -> None:
        from alpaca.data.live.news import NewsDataStream

        backoff = _RECONNECT_MIN_SECONDS
        while True:
            stream = NewsDataStream(self._settings.alpaca_api_key_id, self._settings.alpaca_api_secret_key)
            self._stream = stream
            try:
                # Subscribing before the socket runs just registers the
                # handler; "*" is Alpaca's wildcard for every article.
                stream.subscribe_news(self._on_news, "*")
                self.connected = True
                await stream._run_forever()
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception:
                logger.exception("News stream failed; reconnecting in %.0fs", backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_SECONDS)

    async def _on_news(self, news) -> None:
        self.received += 1
        try:
            item = self._tracker.record(news, datetime.now(UTC))
        except Exception:
            logger.exception("News stream: could not record an article")
            return
        if item is None:
            return
        await self._manager.broadcast(TOPIC, {"type": "news_feed_item", "item": item.model_dump(mode="json")})
