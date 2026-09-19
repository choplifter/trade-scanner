"""The logged-in user's interface preferences (see app.prefs.store).

Deliberately dumb: the browser owns the shape of each value and this only
keeps it per user. The ceilings below are fat-finger guards, not a quota --
a dashboard layout is the biggest thing stored here, a few tens of KB.
"""

import json
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from typing import Any

from app.auth.dependency import get_current_user

router = APIRouter(prefix="/api/prefs", tags=["prefs"])

# The browser's own storage keys ("app:settings", "layout:grid"), so the
# two sides stay readable together.
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}$")
_MAX_VALUE_BYTES = 256 * 1024


@router.get("")
async def get_prefs(request: Request, user: dict = Depends(get_current_user)) -> dict:
    """Every preference this user has stored. An empty object is the normal
    answer for a fresh account -- the browser then keeps its own values and
    pushes them up (see the frontend's api/prefs.ts)."""
    store = request.app.state.user_prefs_store
    return {"prefs": await store.all(user["id"])}


def _checked_key(key: str) -> str:
    if not _KEY_RE.match(key):
        raise HTTPException(status_code=422, detail=f"Not a preference key: {key!r}")
    return key


@router.put("/{key}")
async def put_pref(
    key: str, request: Request, value: Any = Body(...), user: dict = Depends(get_current_user)
) -> dict:
    """Store one preference. The body is the value itself (an object, a
    string, a number) -- not wrapped, so the browser can hand over exactly
    what it would have written to localStorage."""
    key = _checked_key(key)
    encoded = json.dumps(value, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_VALUE_BYTES:
        raise HTTPException(status_code=413, detail=f"{key}: too large to store ({_MAX_VALUE_BYTES // 1024} KB max)")
    store = request.app.state.user_prefs_store
    await store.put(user["id"], key, value)
    return {"key": key}


@router.delete("/{key}")
async def delete_pref(key: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    key = _checked_key(key)
    store = request.app.state.user_prefs_store
    await store.delete(user["id"], key)
    return {"key": key}
