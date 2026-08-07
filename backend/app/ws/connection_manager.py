import asyncio
import logging

import orjson
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Generic topic-keyed pub/sub used by both /ws/scanners and /ws/chart.

    A topic is just a string ("scanner:gainers", "chart:AAPL"); connections
    subscribe/unsubscribe to topics as the frontend's widgets open and close,
    and broadcast() fans a message out to every socket currently on a topic.
    """

    def __init__(self) -> None:
        self._topics: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, topic: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._topics.setdefault(topic, set()).add(websocket)

    async def unsubscribe(self, topic: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._topics.get(topic)
            if sockets is None:
                return
            sockets.discard(websocket)
            if not sockets:
                del self._topics[topic]

    async def unsubscribe_all(self, websocket: WebSocket) -> None:
        async with self._lock:
            for topic in list(self._topics.keys()):
                self._topics[topic].discard(websocket)
                if not self._topics[topic]:
                    del self._topics[topic]

    def has_subscribers(self, topic: str) -> bool:
        return bool(self._topics.get(topic))

    async def broadcast(self, topic: str, message: dict) -> None:
        sockets = list(self._topics.get(topic, ()))
        if not sockets:
            return
        payload = orjson.dumps(message)
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(payload.decode())
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._topics.get(topic, set()).discard(ws)
