"""Password hashing. Pure stdlib -- hashlib.pbkdf2_hmac rather than pulling
in bcrypt/passlib, since this app already leans stdlib-first everywhere
else (sqlite3, zoneinfo, etc.) and PBKDF2 needs nothing beyond it.

200_000 iterations is OWASP's current baseline recommendation for
PBKDF2-HMAC-SHA256 (as of the 2020s guidance) -- high enough to be slow for
an offline brute-force attempt, still sub-millisecond for one real login.
"""

import hashlib
import hmac
import secrets

_ITERATIONS = 200_000
_ALGORITHM = "sha256"


def hash_password(password: str) -> tuple[str, str]:
    """Returns (hash, salt), both hex strings, for storage."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Constant-time comparison -- a timing difference between "wrong
    password" and "right length, wrong bytes" is exactly the side channel
    hmac.compare_digest exists to close."""
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return hmac.compare_digest(digest.hex(), password_hash)
