"""FastAPI dependency gating every protected route on a logged-in session.

Starlette's SessionMiddleware (added in main.py) puts a `.session` dict on
every Request/WebSocket, backed by a signed cookie -- no server-side session
store needed. `user_id` is the only thing kept in it; everything else about
the user is loaded fresh from UserStore on each request, so a display-name
change (were that ever possible) wouldn't need every existing session
invalidated.
"""

from fastapi import HTTPException, Request
from starlette.websockets import WebSocket


async def get_current_user(request: Request) -> dict:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not logged in")
    store = request.app.state.user_store
    user = await store.get_by_id(user_id)
    if user is None:
        # The session cookie outlived the account (deleted from under it) --
        # same 401 as never having logged in, not a 500.
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


async def get_current_user_ws(websocket: WebSocket) -> dict | None:
    """Same lookup for a WebSocket handshake, where raising HTTPException
    isn't the right shape -- callers close the connection themselves on
    None (see ws/chart_ws.py, ws/scanner_ws.py)."""
    user_id = websocket.session.get("user_id")
    if user_id is None:
        return None
    store = websocket.app.state.user_store
    return await store.get_by_id(user_id)
