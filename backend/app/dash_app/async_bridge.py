"""Bridge for calling the backend's async Alpaca-fetching helpers from
synchronous Dash callbacks.

Dash callbacks run as plain Flask view functions inside a2wsgi's
WSGIMiddleware, which executes the wrapped WSGI app in a worker thread --
there is never an asyncio event loop already running on that thread, so a
fresh asyncio.run() per call is the simplest safe way to drive the
existing `async def` helpers in app.market_data.bars.
"""

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)
