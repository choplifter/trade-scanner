"""The user's own Alpaca key pairs -- Settings → Broker. Per user like
the rest of the account-bound routes; secrets travel in one direction
(in), the status answer carries the key id's last characters only.

Storing a key pair verifies it first (one get_account call with those
keys): a typo is refused with Alpaca's reason instead of being kept and
failing every poll afterwards. Connecting a *live* pair asks for the same
typed confirmation as every real-money action.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependency import get_current_user
from app.broker.store import ACCOUNTS
from app.trading.errors import LiveConfirmationRequired, TradingError
from app.trading.guards import LIVE_CONFIRM_HEADER, LIVE_CONFIRMATION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/broker", tags=["broker"])


class ConnectRequest(BaseModel):
    api_key_id: str = Field(min_length=8, max_length=128)
    api_secret_key: str = Field(min_length=8, max_length=256)


def _account_or_422(account: str) -> str:
    if account not in ACCOUNTS:
        raise HTTPException(status_code=422, detail=f"Unknown account: {account}")
    return account


def _resolver(request: Request):
    resolver = getattr(request.app.state, "broker_resolver", None)
    if resolver is None:
        raise HTTPException(status_code=503, detail="Broker credentials are not enabled on this server")
    return resolver


@router.get("/status")
async def broker_status(request: Request, user: dict = Depends(get_current_user)) -> dict:
    settings = request.app.state.settings
    resolver = _resolver(request)
    accounts = {}
    for account in ACCOUNTS:
        try:
            accounts[account] = await resolver.status(user, account)
        except Exception:
            logger.exception("Broker status failed for %s", account)
            accounts[account] = {"account": account, "connected": False, "source": None, "key_hint": None, "error": "status check failed"}
    return {
        "accounts": accounts,
        "is_admin": bool(user.get("is_admin")),
        "trading_enabled": settings.trading_enabled,
        "trading_allow_live": settings.trading_allow_live,
        "market_data_source": "operator",
    }


@router.post("/{account}")
async def connect_broker(
    account: str, body: ConnectRequest, request: Request, user: dict = Depends(get_current_user)
) -> dict:
    account = _account_or_422(account)
    resolver = _resolver(request)
    store = request.app.state.broker_store
    try:
        if account == "live" and (request.headers.get(LIVE_CONFIRM_HEADER) or "").strip() != LIVE_CONFIRMATION:
            raise LiveConfirmationRequired(
                f"Type {LIVE_CONFIRMATION} to connect a real-money account.", field="confirm"
            )
        verified = await resolver.verify(body.api_key_id, body.api_secret_key, account)
        await store.set(
            user["id"], account, body.api_key_id, body.api_secret_key,
            verified_account_number=verified.get("account_number"),
        )
        resolver.invalidate(user["id"])
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Broker connect failed for user %s (%s)", user["id"], account)
        raise HTTPException(status_code=502, detail="Failed to store the broker keys")
    logger.info("User %s connected their %s Alpaca account (%s)", user["id"], account, verified.get("account_number"))
    return {"status": await resolver.status(user, account)}


@router.delete("/{account}")
async def disconnect_broker(account: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    account = _account_or_422(account)
    resolver = _resolver(request)
    removed = await request.app.state.broker_store.delete(user["id"], account)
    resolver.invalidate(user["id"])
    logger.info("User %s disconnected their %s Alpaca keys (removed=%s)", user["id"], account, removed)
    return {"status": await resolver.status(user, account), "removed": removed}
