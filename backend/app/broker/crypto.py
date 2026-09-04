"""Broker secrets at rest. An Alpaca secret key is stored encrypted
(Fernet: AES-128-CBC + HMAC, from the `cryptography` package) under a key
derived from BROKER_ENCRYPTION_KEY, or -- when that is not set -- from
SESSION_SECRET_KEY, through HKDF with a fixed purpose tag so the two uses
of the session secret never share key material.

Rotating the key material makes every stored secret unreadable; the
resolver treats that as "not connected" and the user enters the keys
again. Keys are never logged (app.core.logging redacts `secret` params in
URLs; nothing here formats a secret into a message).
"""

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import Settings

_PURPOSE = b"stocks-in-play broker keys v1"


class SecretUnreadable(Exception):
    """The stored token does not decrypt under the current key material."""


class SecretBox:
    def __init__(self, key_material: str) -> None:
        if not key_material:
            raise ValueError("Broker secrets need key material (BROKER_ENCRYPTION_KEY or SESSION_SECRET_KEY)")
        derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_PURPOSE).derive(
            key_material.encode("utf-8")
        )
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise SecretUnreadable() from exc


def secret_box_from_settings(settings: Settings) -> SecretBox:
    return SecretBox(settings.broker_encryption_key or settings.session_secret_key)
