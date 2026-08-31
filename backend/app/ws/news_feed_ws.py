"""Push-only WS half of the live cross-symbol news feed. Deliberately no
snapshot-on-subscribe (unlike ws/scanner_ws.py and ws/replay_ws.py) -- this
feed has exactly one global topic, not a per-name/per-user one, and
GET /api/news-feed/recent (routers/news_feed.py) already seeds a freshly
mounted widget once; this socket only ever appends items after that,
avoiding a redundant tracker read on every subscribe. A widget that mounts
in the gap between the REST response and this socket completing its
handshake could in principle miss an item broadcast in that window --
accepted as a best-effort feed, not something worth a snapshot-on-subscribe
round trip to close.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.dependency import get_current_user_ws

logger = logging.getLogger(__name__)

router = APIRouter()

_TOPIC = "news:feed"


@router.websocket("/ws/news-feed")
async def news_feed_ws(websocket: WebSocket) -> None:
    if await get_current_user_ws(websocket) is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager = websocket.app.state.connection_manager

    try:
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") == "subscribe":
                await manager.subscribe(_TOPIC, websocket)
            elif msg.get("type") == "unsubscribe":
                await manager.unsubscribe(_TOPIC, websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("news_feed_ws connection error")
    finally:
        await manager.unsubscribe_all(websocket)
