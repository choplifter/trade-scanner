"""One multiplexed socket per browser tab for history-replay updates --
the counterpart to ws.scanner_ws, but per-user: a replay session is never
shared, so topics are scoped by the authenticated socket's own user_id
(never a client-supplied one) -- see app.replay.loop._topic.

{"type":"subscribe"|"unsubscribe","scanner":"gainers"} is the only message
this speaks; app.replay.loop.run_replay_pacing_loop is the sole broadcaster.
An initial snapshot is sent on subscribe the same way scanner_ws does, so a
tab opened mid-session doesn't wait for the next pacing tick to see rows.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.dependency import get_current_user_ws
from app.replay.loop import replay_update_payload, topic_for

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/replay")
async def replay_ws(websocket: WebSocket) -> None:
    user = await get_current_user_ws(websocket)
    if user is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager = websocket.app.state.connection_manager
    replay_store = websocket.app.state.replay_store
    replay_engines = websocket.app.state.replay_engines
    user_id = user["id"]

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            scanner = msg.get("scanner")
            if not scanner:
                continue
            topic = topic_for(user_id, scanner)

            if msg_type == "subscribe":
                await manager.subscribe(topic, websocket)
                session = await replay_store.get(user_id)
                engine = replay_engines.get(user_id)
                if session is not None and engine is not None:
                    as_of = datetime.fromisoformat(session["as_of"])
                    rows = engine.snapshot_views(as_of, websocket.app.state.settings.scanner_min_dollar_volume).get(
                        scanner, []
                    )
                    await websocket.send_json(replay_update_payload(scanner, as_of, rows))
                else:
                    await websocket.send_json(replay_update_payload(scanner, datetime.now(UTC), []))
            elif msg_type == "unsubscribe":
                await manager.unsubscribe(topic, websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("replay_ws connection error")
    finally:
        await manager.unsubscribe_all(websocket)
