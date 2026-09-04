"""Admin-created user accounts. Same file/conventions as every other store
here (stdlib sqlite3, fresh connection per call via asyncio.to_thread, WAL
mode). No self-registration endpoint exists anywhere -- create_user is
called only from scripts/create_user.py, by whoever administers this
deployment.

`is_admin` marks the operator: the one login whose broker calls may fall
back to the Alpaca keys in .env (see app.broker.resolver). Every other
user connects their own account in Settings. A database from before the
flag existed gets its lowest-id user promoted once, since that is the
person who set the deployment up.
"""

import asyncio
import sqlite3
from datetime import UTC, datetime

from app.auth.security import hash_password, verify_password

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0
);
"""


class UsernameTaken(Exception):
    pass


def _public(row: dict) -> dict:
    """Strips password_hash/password_salt -- the only fields on this table
    that must never reach a response body. get_by_username keeps the full
    row (verify_login needs the hash to check against); everything that
    ends up in a request handler's hands goes through this first."""
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row.get("is_admin", 0)),
    }


class UserStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            if "is_admin" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            # No admin yet (fresh flag, or an older database): the first
            # account created is the operator's.
            has_admin = conn.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
            if has_admin is None:
                conn.execute(
                    "UPDATE users SET is_admin = 1 WHERE id = (SELECT MIN(id) FROM users)"
                )

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    def _get_by_username_sync(self, username: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    async def get_by_username(self, username: str) -> dict | None:
        return await asyncio.to_thread(self._get_by_username_sync, username)

    def _get_by_id_sync(self, user_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _public(dict(row)) if row else None

    async def get_by_id(self, user_id: int) -> dict | None:
        """Used everywhere a session is resolved back to a user (the auth
        dependency, /me) -- never needs the password hash, so this is the
        one that's safe to hand straight to a response body."""
        return await asyncio.to_thread(self._get_by_id_sync, user_id)

    def _list_sync(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [_public(dict(r)) for r in rows]

    async def list_users(self) -> list[dict]:
        """Every account, public fields only -- the trigger loop resolves
        each trigger's owner through this."""
        return await asyncio.to_thread(self._list_sync)

    def _create_user_sync(
        self, username: str, password: str, display_name: str, now: datetime, is_admin: bool
    ) -> dict:
        password_hash, salt = hash_password(password)
        with self._connect() as conn:
            existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if existing is not None:
                raise UsernameTaken(username)
            # The very first account is the operator's even without --admin.
            first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, password_salt, display_name, created_at, is_admin) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, password_hash, salt, display_name, now.isoformat(), 1 if (is_admin or first) else 0),
            )
            user_id = cursor.lastrowid
        return _public(
            {"id": user_id, "username": username, "display_name": display_name, "is_admin": is_admin or first}
        )

    async def create_user(self, username: str, password: str, display_name: str, *, is_admin: bool = False) -> dict:
        """The only way an account gets created -- called from
        scripts/create_user.py, never from a request handler."""
        return await asyncio.to_thread(
            self._create_user_sync, username, password, display_name, datetime.now(UTC), is_admin
        )

    def _set_admin_sync(self, user_id: int, is_admin: bool) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, user_id))

    async def set_admin(self, user_id: int, is_admin: bool) -> None:
        await asyncio.to_thread(self._set_admin_sync, user_id, is_admin)

    async def verify_login(self, username: str, password: str) -> dict | None:
        row = await self.get_by_username(username)
        if row is None:
            return None
        if not verify_password(password, row["password_hash"], row["password_salt"]):
            return None
        return _public(row)
