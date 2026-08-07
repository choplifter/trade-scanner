import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/chart")
async def chart_ws(websocket: WebSocket) -> None:
    """One multiplexed socket per browser tab. Client sends
    {"type":"subscribe"|"unsubscribe","symbol":"AAPL"} as chart widgets
    open/close; server ref-counts the underlying live Alpaca subscription via
    StreamManager and tags outgoing bar messages with symbol for the client
    to demux to the right widget.
    """
    await websocket.accept()
    manager = websocket.app.state.connection_manager
    stream_manager = websocket.app.state.stream_manager
    subscribed_symbols: set[str] = set()

    try:
        while True:
            msg = await websocket.receive_json()
            symbol = (msg.get("symbol") or "").upper()
            if not symbol:
                continue
            topic = f"chart:{symbol}"

            if msg.get("type") == "subscribe":
                try:
                    await stream_manager.subscribe(symbol)
                except ValueError as exc:
                    await websocket.send_json(
                        {"type": "error", "symbol": symbol, "message": str(exc)}
                    )
                    continue
                await manager.subscribe(topic, websocket)
                subscribed_symbols.add(symbol)
            elif msg.get("type") == "unsubscribe":
                await manager.unsubscribe(topic, websocket)
                subscribed_symbols.discard(symbol)
                await stream_manager.unsubscribe(symbol)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("chart_ws connection error")
    finally:
        await manager.unsubscribe_all(websocket)
        for symbol in subscribed_symbols:
            await stream_manager.unsubscribe(symbol)
