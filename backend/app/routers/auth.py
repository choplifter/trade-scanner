"""Login/logout/whoami. The only unauthenticated routes in the app (see
main.py -- every other router requires get_current_user).
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


@router.post("/login")
async def login(body: LoginRequest, request: Request) -> dict:
    store = request.app.state.user_store
    user = await store.verify_login(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Wrong username or password")
    request.session["user_id"] = user["id"]
    return {"user": user}


@router.post("/logout")
async def logout(request: Request) -> dict:
    request.session.clear()
    return {}


@router.get("/me")
async def me(request: Request) -> dict:
    # Not Depends(get_current_user) here: that would turn "am I logged in"
    # into a 401 the frontend has to treat as an expected, non-error
    # response on every page load. Resolve the same way, but return
    # {user: None} instead of raising.
    user_id = request.session.get("user_id")
    if user_id is None:
        return {"user": None}
    found = await request.app.state.user_store.get_by_id(user_id)
    return {"user": found}
