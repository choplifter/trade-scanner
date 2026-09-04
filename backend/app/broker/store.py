"""Per-user Alpaca key pairs -- same file and conventions as every other
store here (stdlib sqlite3, a fresh connection per call via
asyncio.to_thread, WAL mode). One row per (user, account): the key id in
the clear (it is not a secret and the UI shows its last characters), the
secret encrypted by app.broker.crypto.SecretBox.

Only get() ever hands the secret back, and only to the resolver that
builds a TradingClient from it; list_for_user() is what a response body
sees and carries no secret.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime

from app.broker.crypto import SecretBox, SecretUnreadable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_broker_keys (
    user_id INTEGER NOT NULL,
    account TEXT NOT NULL,
    api_key_id TEXT NOT NULL,
    secret_encrypted TEXT NOT NULL,
    key_hint TEXT NOT NULL,
    verified_account_number TEXT,
    verified_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, account)
);
"""

ACCOUNTS = ("paper", "live")


def key_hint(api_key_id: str) -> str:
    """What the UI shows of a key id: its last four characters."""
    return api_key_id[-4:] if len(api_key_id) >= 4 else api_key_id


class BrokerStore:
    def __init__(self, db_path: str, box: SecretBox) -> None:
        self.db_path = db_path
        self._box = box

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    # --- writes -------------------------------------------------------------

    def _set_sync(
        self, user_id: int, account: str, api_key_id: str, secret: str, verified_account_number: str | None, now: str
    ) -> dict:
        token = self._box.encrypt(secret)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_broker_keys (user_id, account, api_key_id, secret_encrypted, key_hint, "
                "verified_account_number, verified_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, account) DO UPDATE SET api_key_id = excluded.api_key_id, "
                "secret_encrypted = excluded.secret_encrypted, key_hint = excluded.key_hint, "
                "verified_account_number = excluded.verified_account_number, verified_at = excluded.verified_at, "
                "updated_at = excluded.updated_at",
                (user_id, account, api_key_id, token, key_hint(api_key_id), verified_account_number, now, now),
            )
        return {
            "user_id": user_id,
            "account": account,
            "api_key_id": api_key_id,
            "key_hint": key_hint(api_key_id),
            "verified_account_number": verified_account_number,
            "verified_at": now,
            "updated_at": now,
        }

    async def set(
        self, user_id: int, account: str, api_key_id: str, secret: str, *, verified_account_number: str | None = None
    ) -> dict:
        if account not in ACCOUNTS:
            raise ValueError(f"Unknown account: {account!r}")
        return await asyncio.to_thread(
            self._set_sync, user_id, account, api_key_id.strip(), secret.strip(), verified_account_number,
            datetime.now(UTC).isoformat(),
        )

    def _delete_sync(self, user_id: int, account: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM user_broker_keys WHERE user_id = ? AND account = ?", (user_id, account))
            return cur.rowcount > 0

    async def delete(self, user_id: int, account: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, user_id, account)

    # --- reads --------------------------------------------------------------

    def _get_sync(self, user_id: int, account: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_broker_keys WHERE user_id = ? AND account = ?", (user_id, account)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["api_secret_key"] = self._box.decrypt(d.pop("secret_encrypted"))
        except SecretUnreadable:
            # Key material rotated: the row is dead weight until re-entered.
            return None
        return d

    async def get(self, user_id: int, account: str) -> dict | None:
        """The key pair with the secret in the clear -- for the resolver only."""
        return await asyncio.to_thread(self._get_sync, user_id, account)

    def _list_for_user_sync(self, user_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, account, api_key_id, key_hint, verified_account_number, verified_at, updated_at "
                "FROM user_broker_keys WHERE user_id = ? ORDER BY account",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    async def list_for_user(self, user_id: int) -> list[dict]:
        """What a response body may see: no secret, ever."""
        return await asyncio.to_thread(self._list_for_user_sync, user_id)

    def _user_ids_with_keys_sync(self) -> set[int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT user_id FROM user_broker_keys").fetchall()
        return {int(r[0]) for r in rows}

    async def user_ids_with_keys(self) -> set[int]:
        return await asyncio.to_thread(self._user_ids_with_keys_sync)
