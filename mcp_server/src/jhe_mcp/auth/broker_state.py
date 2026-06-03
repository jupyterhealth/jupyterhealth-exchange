from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class StateError(Exception):
    """Raised when a state/code token is invalid, tampered, or expired."""


def _fernet(key: str) -> Fernet:
    # Derive a valid 32-byte url-safe Fernet key from an arbitrary secret.
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encode(key: str, payload: dict[str, Any]) -> str:
    token = _fernet(key).encrypt(json.dumps(payload).encode("utf-8"))
    return token.decode("ascii")


def decode(key: str, token: str, max_age: int | None) -> dict[str, Any]:
    # max_age=None disables the Fernet TTL check (token never expires).
    try:
        raw = _fernet(key).decrypt(token.encode("ascii"), ttl=max_age)
    except (InvalidToken, ValueError) as exc:
        raise StateError("invalid, tampered, or expired token") from exc
    return json.loads(raw)
