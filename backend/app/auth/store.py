"""Admin-created user accounts. Same file/conventions as every other store
here (stdlib sqlite3, fresh connection per call via asyncio.to_thread, WAL
mode). No self-registration endpoint exists anywhere -- create_user is
called only from scripts/create_user.py, by whoever administers this
deployment.
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
    created_at TEXT NOT NULL
);
"""


class UsernameTaken(Exception):
    pass


def _public(row: dict) -> dict:
    """Strips password_hash/password_salt -- the only fields on this table
    that must never reach a response body. get_by_username keeps the full
    row (verify_login needs the hash to check against); everything that
    ends up in a request handler's hands goes through this first."""
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"]}


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

    def _create_user_sync(self, username: str, password: str, display_name: str, now: datetime) -> dict:
        password_hash, salt = hash_password(password)
        with self._connect() as conn:
            existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if existing is not None:
                raise UsernameTaken(username)
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, password_salt, display_name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, salt, display_name, now.isoformat()),
            )
            user_id = cursor.lastrowid
        return _public({"id": user_id, "username": username, "display_name": display_name})

    async def create_user(self, username: str, password: str, display_name: str) -> dict:
        """The only way an account gets created -- called from
        scripts/create_user.py, never from a request handler."""
        return await asyncio.to_thread(self._create_user_sync, username, password, display_name, datetime.now(UTC))

    async def verify_login(self, username: str, password: str) -> dict | None:
        row = await self.get_by_username(username)
        if row is None:
            return None
        if not verify_password(password, row["password_hash"], row["password_salt"]):
            return None
        return _public(row)
