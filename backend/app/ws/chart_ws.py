import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.dependency import get_current_user_ws
from app.options.occ import try_parse_occ

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
    # Checked before accept() so an unauthenticated attempt fails the
    # handshake cleanly instead of opening a socket just to close it --
    # same "nobody unauthenticated sees anything" posture as every REST
    # route (see main.py).
    if await get_current_user_ws(websocket) is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager = websocket.app.state.connection_manager
    stream_manager = websocket.app.state.stream_manager
    option_stream_manager = websocket.app.state.option_stream_manager
    subscribed_symbols: set[str] = set()

    # Option contracts (OCC symbols) ride the option stream, stocks the
    # stock stream; both publish on chart:{symbol}.
    def manager_for(symbol: str):
        return option_stream_manager if try_parse_occ(symbol) is not None else stream_manager

    try:
        while True:
            msg = await websocket.receive_json()
            symbol = (msg.get("symbol") or "").upper()
            if not symbol:
                continue
            topic = f"chart:{symbol}"

            if msg.get("type") == "subscribe":
                try:
                    await manager_for(symbol).subscribe(symbol)
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
                await manager_for(symbol).unsubscribe(symbol)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("chart_ws connection error")
    finally:
        await manager.unsubscribe_all(websocket)
        for symbol in subscribed_symbols:
            await manager_for(symbol).unsubscribe(symbol)
