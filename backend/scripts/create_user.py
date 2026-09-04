"""Create a login for this dashboard. The only way an account gets made --
there is no signup endpoint (see app.routers.auth), by design: this is a
small trusted group, not an open product.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.create_user <username> "<display name>"

Prompts for a password (never accepted as a command-line argument, so it
never lands in shell history).
"""

import argparse
import asyncio
import getpass
import sys

from app.auth.store import UsernameTaken, UserStore
from app.core.config import get_settings


def _prompt_password(label: str) -> str:
    # getpass.getpass() talks to the console directly on Windows (msvcrt),
    # which hangs rather than reading redirected/piped stdin -- fall back to
    # a plain (visible) prompt whenever stdin isn't a real terminal, the
    # same accommodation getpass itself already makes on POSIX.
    if sys.stdin.isatty():
        return getpass.getpass(f"{label}: ")
    return input(f"{label}: ")


async def _main(username: str, display_name: str, is_admin: bool = False) -> None:
    settings = get_settings()
    store = UserStore(settings.scanner_history_db_path)
    await store.init_schema()

    password = _prompt_password("Password")
    confirm = _prompt_password("Confirm password")
    if password != confirm:
        print("Passwords did not match.")
        return
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return

    try:
        user = await store.create_user(username, password, display_name, is_admin=is_admin)
    except UsernameTaken:
        print(f"Username {username!r} is already taken.")
        return

    role = "admin -- may trade on the .env Alpaca keys" if user["is_admin"] else "user -- connects their own Alpaca keys in Settings"
    print(f"Created user {user['username']!r} (id={user['id']}, display_name={user['display_name']!r}, {role}).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("display_name")
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Operator account: broker calls may fall back to the Alpaca keys in .env. The first account is always one.",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.username, args.display_name, args.admin))
