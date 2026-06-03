import time
from pathlib import Path

import pytest
from jhe_mcp.auth.token_cache import (
    CachedToken,
    TokenCache,
    TokenCacheMiss,
)


def _make_cached(access="a", refresh="r", expires_in=3600) -> CachedToken:
    return CachedToken(
        access_token=access,
        refresh_token=refresh,
        expires_at=int(time.time()) + expires_in,
    )


def test_save_and_load_roundtrip(tmp_path: Path):
    cache_file = tmp_path / "token.json"
    cache = TokenCache(cache_file)
    cache.save(_make_cached(access="x", refresh="y"))
    loaded = cache.load()
    assert loaded.access_token == "x"
    assert loaded.refresh_token == "y"


def test_load_raises_when_no_file(tmp_path: Path):
    cache_file = tmp_path / "missing.json"
    cache = TokenCache(cache_file)
    with pytest.raises(TokenCacheMiss):
        cache.load()


def test_save_writes_0600_perms(tmp_path: Path):
    cache_file = tmp_path / "token.json"
    cache = TokenCache(cache_file)
    cache.save(_make_cached())
    mode = cache_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_needs_refresh_near_expiry(tmp_path: Path):
    cache = TokenCache(tmp_path / "t.json")
    token = CachedToken(
        access_token="a",
        refresh_token="r",
        expires_at=int(time.time()) + 30,
    )
    assert cache.needs_refresh(token, leeway_seconds=60) is True
    token2 = CachedToken(
        access_token="a",
        refresh_token="r",
        expires_at=int(time.time()) + 300,
    )
    assert cache.needs_refresh(token2, leeway_seconds=60) is False


def test_load_rejects_malformed_json(tmp_path: Path):
    cache_file = tmp_path / "broken.json"
    cache_file.write_text("{not json")
    cache = TokenCache(cache_file)
    with pytest.raises(TokenCacheMiss):
        cache.load()
