from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path


class TokenCacheMiss(Exception):
    """Raised when the cache file is absent or unreadable."""


@dataclass(frozen=True)
class CachedToken:
    access_token: str
    refresh_token: str | None
    expires_at: int  # unix epoch seconds


class TokenCache:
    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def default(cls) -> TokenCache:
        home = Path.home() / ".jhe_mcp"
        if not home.exists():
            home.mkdir(parents=True)
            os.chmod(home, 0o700)
        return cls(home / "token_cache.json")

    def save(self, token: CachedToken) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(token)))
        os.chmod(tmp, 0o600)
        tmp.replace(self._path)

    def load(self) -> CachedToken:
        try:
            raw = self._path.read_text()
        except FileNotFoundError as exc:
            raise TokenCacheMiss(str(self._path)) from exc
        try:
            data = json.loads(raw)
            return CachedToken(**data)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise TokenCacheMiss(f"malformed cache at {self._path}: {exc}") from exc

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass

    def needs_refresh(self, token: CachedToken, leeway_seconds: int = 60) -> bool:
        return time.time() + leeway_seconds >= token.expires_at
