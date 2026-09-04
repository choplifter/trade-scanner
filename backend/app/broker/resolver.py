"""Which Alpaca account a request talks to.

A user's own key pair for the account comes first. Without one, only an
admin (the operator, see app.auth.store) falls back to the keys in .env;
everyone else gets None, which the routers turn into a "connect your
broker" answer -- a guest must never trade on the operator's account by
default. Market data is not decided here: it always runs on the
operator's keys (app.alpaca.client.AlpacaClients).

TradingClients are cached per (user, account, key id): a client is a
requests session, cheap but not free, and the trigger loop asks for one
every two seconds.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from app.broker.store import BrokerStore, key_hint
from app.core.config import Settings
from app.trading.errors import OrderRejected

logger = logging.getLogger(__name__)

Source = str  # "user" | "env"


@dataclass(frozen=True)
class BrokerCredentials:
    key_id: str
    secret: str
    paper: bool
    source: Source
    key_hint: str
    verified_account_number: str | None = None


def _default_client_factory(key_id: str, secret: str, paper: bool):
    from alpaca.trading.client import TradingClient

    return TradingClient(api_key=key_id, secret_key=secret, paper=paper)


class BrokerResolver:
    def __init__(self, settings: Settings, store: BrokerStore, *, client_factory: Callable | None = None) -> None:
        self._settings = settings
        self._store = store
        self._factory = client_factory or _default_client_factory
        self._cache: dict[tuple[int, str, str], object] = {}

    # --- credentials -----------------------------------------------------------

    def env_credentials(self, account: str) -> BrokerCredentials | None:
        """The operator's own key pair from .env, for the admin."""
        s = self._settings
        if account == "paper" and s.has_credentials:
            return BrokerCredentials(
                key_id=s.alpaca_api_key_id, secret=s.alpaca_api_secret_key, paper=s.alpaca_paper,
                source="env", key_hint=key_hint(s.alpaca_api_key_id),
            )
        if account == "live" and s.has_live_credentials:
            return BrokerCredentials(
                key_id=s.alpaca_live_api_key_id, secret=s.alpaca_live_api_secret_key, paper=False,
                source="env", key_hint=key_hint(s.alpaca_live_api_key_id),
            )
        return None

    async def credentials(self, user: dict, account: str) -> BrokerCredentials | None:
        row = await self._store.get(int(user["id"]), account)
        if row is not None:
            return BrokerCredentials(
                key_id=row["api_key_id"], secret=row["api_secret_key"], paper=account == "paper",
                source="user", key_hint=row["key_hint"], verified_account_number=row.get("verified_account_number"),
            )
        if user.get("is_admin"):
            return self.env_credentials(account)
        return None

    async def availability(self, user: dict) -> dict[str, bool]:
        """Which accounts this user can reach -- what the guards ask."""
        return {
            "paper": await self.credentials(user, "paper") is not None,
            "live": await self.credentials(user, "live") is not None,
        }

    # --- clients ------------------------------------------------------------

    async def client(self, user: dict, account: str):
        creds = await self.credentials(user, account)
        if creds is None:
            return None
        key = (int(user["id"]), account, creds.key_id)
        client = self._cache.get(key)
        if client is None:
            client = self._cache[key] = self._factory(creds.key_id, creds.secret, creds.paper)
        return client

    def invalidate(self, user_id: int) -> None:
        for key in [k for k in self._cache if k[0] == user_id]:
            self._cache.pop(key, None)

    # --- verification -----------------------------------------------------------

    async def verify(self, key_id: str, secret: str, account: str) -> dict:
        """Ask Alpaca who these keys belong to. A refusal becomes an
        OrderRejected the UI can show; nothing is stored here."""
        client = self._factory(key_id.strip(), secret.strip(), account == "paper")
        try:
            raw = await asyncio.to_thread(client.get_account)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            reason = "Alpaca rejected these keys" if status in (401, 403) else "Alpaca could not be reached"
            logger.info("Broker key verification failed (%s account): %s", account, status or type(exc).__name__)
            raise OrderRejected(f"{reason} ({status or type(exc).__name__}).", field="api_secret_key") from exc
        return account_summary(raw)

    async def status(self, user: dict, account: str) -> dict:
        """What the Settings dialog shows for one account."""
        creds = await self.credentials(user, account)
        if creds is None:
            return {"account": account, "connected": False, "source": None, "key_hint": None}
        out = {
            "account": account,
            "connected": True,
            "source": creds.source,
            "key_hint": creds.key_hint,
            "account_number": creds.verified_account_number,
            "status": None,
            "options_trading_level": None,
            "error": None,
        }
        client = await self.client(user, account)
        try:
            raw = await asyncio.to_thread(client.get_account)  # type: ignore[union-attr]
            out.update(account_summary(raw))
        except Exception as exc:
            out["connected"] = False
            out["error"] = f"Alpaca refused the stored keys ({getattr(exc, 'status_code', None) or type(exc).__name__})."
        return out


async def operator_data_credentials(user_store, store: BrokerStore) -> BrokerCredentials | None:
    """The key pair market data runs on: the first admin's stored *paper*
    pair (Settings → Broker), so the operator never has to touch .env to
    rotate keys. None means .env decides. The data subscription is bound
    to the account, which is the same one either way."""
    admins = [u for u in await user_store.list_users() if u.get("is_admin")]
    if not admins:
        return None
    admin = min(admins, key=lambda u: int(u["id"]))
    row = await store.get(int(admin["id"]), "paper")
    if row is None:
        return None
    return BrokerCredentials(
        key_id=row["api_key_id"], secret=row["api_secret_key"], paper=True, source="user",
        key_hint=row["key_hint"], verified_account_number=row.get("verified_account_number"),
    )


def account_summary(raw) -> dict:
    """The few account fields the broker tab shows."""
    get = (lambda k: getattr(raw, k, None)) if not isinstance(raw, dict) else raw.get
    level = get("options_trading_level")
    if level is None:
        level = get("options_approved_level")
    status = get("status")
    return {
        "account_number": get("account_number"),
        "status": str(getattr(status, "value", status)) if status is not None else None,
        "options_trading_level": int(level) if level is not None else None,
        "buying_power": float(get("buying_power")) if get("buying_power") not in (None, "") else None,
        "equity": float(get("equity")) if get("equity") not in (None, "") else None,
    }
