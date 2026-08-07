import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/scanners")
async def scanners_ws(websocket: WebSocket) -> None:
    """One multiplexed socket per browser tab. Client sends
    {"type":"subscribe"|"unsubscribe","scanner":"gainers"} as scanner widgets
    open/close; server replies to a subscribe with the current cached rows
    immediately, then pushes scanner_update messages on each poll cycle.
    """
    await websocket.accept()
    manager = websocket.app.state.connection_manager
    engine = websocket.app.state.scanner_engine

    try:
        while True:
            msg = await websocket.receive_json()
            scanner = msg.get("scanner")
            if not scanner:
                continue
            topic = f"scanner:{scanner}"

            if msg.get("type") == "subscribe":
                await manager.subscribe(topic, websocket)
                rows = engine.snapshot_view(scanner)
                await websocket.send_json(
                    {
                        "type": "scanner_update",
                        "scanner": scanner,
                        "session": engine.session,
                        "rows": [r.model_dump(mode="json") for r in rows],
                    }
                )
            elif msg.get("type") == "unsubscribe":
                await manager.unsubscribe(topic, websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("scanners_ws connection error")
    finally:
        await manager.unsubscribe_all(websocket)
