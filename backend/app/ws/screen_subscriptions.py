"""Per-connection live screens.

ConnectionManager is topic-keyed: every subscriber to "scanner:gainers" gets
the identical payload, computed once. That is exactly right for a fixed view
and exactly wrong for a screener, where each connection is asking a different
question -- so a screen can't be a topic, and its result can't be shared.

This holds one screen per socket and re-evaluates them on each poll tick, so
a user-defined screen updates live the same way the fixed views always have.
Evaluation is a filter and sort over at most a couple of thousand rows, which
is microseconds; the cost that would matter at scale is the per-connection
*fan-out*, not the per-connection compute, and this is a personal dashboard
with a handful of sockets.
"""

import asyncio
import logging

import orjson
from fastapi import WebSocket

from app.scanners import screener

logger = logging.getLogger(__name__)


class ScreenSubscriptions:
    def __init__(self) -> None:
        self._screens: dict[WebSocket, screener.Screen] = {}
        self._lock = asyncio.Lock()

    async def set(self, websocket: WebSocket, screen: screener.Screen) -> None:
        """One screen per socket -- re-sending replaces rather than adds. A
        widget that lets you edit filters emits a new screen on every change,
        and accumulating them would mean a socket receiving several
        contradictory result sets per tick.
        """
        async with self._lock:
            self._screens[websocket] = screen

    async def remove(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._screens.pop(websocket, None)

    def active(self) -> list[tuple[WebSocket, screener.Screen]]:
        return list(self._screens.items())

    def any_active(self) -> bool:
        return bool(self._screens)

    async def broadcast(self, results: list[tuple[WebSocket, dict]]) -> None:
        """Send each socket its own result. Dead sockets are dropped here
        rather than left to accumulate -- same self-healing as
        ConnectionManager.broadcast, since a closed tab never sends an
        explicit unsubscribe.
        """
        dead: list[WebSocket] = []
        for websocket, payload in results:
            try:
                await websocket.send_text(orjson.dumps(payload).decode())
            except Exception:
                dead.append(websocket)
        if dead:
            async with self._lock:
                for websocket in dead:
                    self._screens.pop(websocket, None)
