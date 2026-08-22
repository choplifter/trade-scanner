import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.scanners import screener
from app.scanners.screener_service import screen_live_rows

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/scanners")
async def scanners_ws(websocket: WebSocket) -> None:
    """One multiplexed socket per browser tab, speaking two message types.

    {"type":"subscribe"|"unsubscribe","scanner":"gainers"} is the original
    named-view protocol: shared, topic-keyed, one payload computed for every
    subscriber. Still used for "premarket_gainers", which is a frozen 09:30
    snapshot rather than a question about the current rows, so it cannot be
    expressed as a screen at all.

    {"type":"screen","screen":{...}} subscribes this socket to a *user-defined*
    screen (see app.scanners.screener). Each connection gets its own,
    re-evaluated on every poll tick, so a custom screen updates live exactly
    like the fixed views do. Sending another replaces the previous one.

    Both can be active on one socket at once -- they're independent
    registries, and the client decides which it needs.
    """
    await websocket.accept()
    manager = websocket.app.state.connection_manager
    engine = websocket.app.state.scanner_engine
    settings = websocket.app.state.settings
    screens = websocket.app.state.screen_subscriptions

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if msg_type == "screen":
                try:
                    screen = screener.Screen(**(msg.get("screen") or {}))
                except ValidationError:
                    # A malformed screen shouldn't kill the socket -- the
                    # client can simply send a corrected one.
                    logger.warning("Ignoring malformed screen payload")
                    continue
                await screens.set(websocket, screen)
                # Reply immediately rather than making the client wait for the
                # next poll tick, matching what subscribe does below.
                await websocket.send_json(
                    {"type": "screen_update", **screen_live_rows(engine, settings, screen)}
                )
                continue

            if msg_type == "unscreen":
                await screens.remove(websocket)
                continue

            scanner = msg.get("scanner")
            if not scanner:
                continue
            topic = f"scanner:{scanner}"

            if msg_type == "subscribe":
                await manager.subscribe(topic, websocket)
                rows = engine.snapshot_view(scanner)
                # Shaped by the engine, not here, so this reply and the
                # run_loop broadcast cannot disagree about what a
                # scanner_update contains -- they already had.
                await websocket.send_json(engine.scanner_update_payload(scanner, rows))
            elif msg_type == "unsubscribe":
                await manager.unsubscribe(topic, websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("scanners_ws connection error")
    finally:
        await manager.unsubscribe_all(websocket)
        # A closed tab never sends an explicit unscreen, so without this the
        # engine would keep evaluating a screen for a socket that's gone.
        await screens.remove(websocket)
