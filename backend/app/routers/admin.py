"""User management for the operator -- Settings → Users, admins only.

Accounts used to be created from scripts/create_user.py alone; that
still works, this is the same UserStore behind a router. Every endpoint
needs a logged-in admin (users.is_admin); a 403 otherwise. Guard rails:
an admin cannot delete themselves or drop their own admin flag, and the
last admin cannot be demoted, so the deployment never ends up with no one
able to manage it.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependency import get_current_user
from app.auth.store import UsernameTaken

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
_MIN_PASSWORD = 8


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    return user


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=_MIN_PASSWORD, max_length=128)
    is_admin: bool = False


class UpdateUserRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    is_admin: bool | None = None
    password: str | None = Field(default=None, min_length=_MIN_PASSWORD, max_length=128)


async def _with_broker(request: Request, users: list[dict]) -> list[dict]:
    """Each user with which accounts they have keys for -- the hint the
    operator needs to see who still has to connect a broker."""
    store = getattr(request.app.state, "broker_store", None)
    out = []
    for user in users:
        keys = await store.list_for_user(user["id"]) if store is not None else []
        out.append({**user, "broker": {k["account"]: k["key_hint"] for k in keys}})
    return out


@router.get("/users")
async def list_users(request: Request, _admin: dict = Depends(require_admin)) -> dict:
    users = await request.app.state.user_store.list_users()
    return {"users": await _with_broker(request, users)}


@router.post("/users")
async def create_user(body: CreateUserRequest, request: Request, admin: dict = Depends(require_admin)) -> dict:
    username = body.username.strip().lower()
    if not _USERNAME.match(username):
        raise HTTPException(
            status_code=422, detail="Username: 3-32 characters, lowercase letters, digits, '.', '_' or '-'"
        )
    try:
        user = await request.app.state.user_store.create_user(
            username, body.password, body.display_name.strip(), is_admin=body.is_admin
        )
    except UsernameTaken:
        raise HTTPException(status_code=422, detail=f"Username {username!r} is already taken")
    logger.info("Admin %s created user %s (admin=%s)", admin["username"], username, body.is_admin)
    return {"user": {**user, "broker": {}}}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int, body: UpdateUserRequest, request: Request, admin: dict = Depends(require_admin)
) -> dict:
    store = request.app.state.user_store
    target = await store.get_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No such user")
    if body.is_admin is not None and body.is_admin != target["is_admin"]:
        if user_id == admin["id"] and not body.is_admin:
            raise HTTPException(status_code=422, detail="You cannot drop your own admin flag")
        if not body.is_admin:
            admins = [u for u in await store.list_users() if u["is_admin"]]
            if len(admins) <= 1:
                raise HTTPException(status_code=422, detail="The last admin cannot be demoted")
        await store.set_admin(user_id, body.is_admin)
    if body.display_name is not None:
        await store.set_display_name(user_id, body.display_name.strip())
    if body.password is not None:
        await store.set_password(user_id, body.password)
        logger.info("Admin %s reset the password of user %s", admin["username"], target["username"])
    updated = await store.get_by_id(user_id)
    return {"user": (await _with_broker(request, [updated]))[0]}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, request: Request, admin: dict = Depends(require_admin)) -> dict:
    if user_id == admin["id"]:
        raise HTTPException(status_code=422, detail="You cannot delete your own login")
    store = request.app.state.user_store
    target = await store.get_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No such user")
    broker_store = getattr(request.app.state, "broker_store", None)
    if broker_store is not None:
        for account in ("paper", "live"):
            await broker_store.delete(user_id, account)
    resolver = getattr(request.app.state, "broker_resolver", None)
    if resolver is not None:
        resolver.invalidate(user_id)
    await store.delete_user(user_id)
    logger.info("Admin %s deleted user %s", admin["username"], target["username"])
    return {"deleted": user_id}
