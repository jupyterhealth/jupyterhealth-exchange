"""Validate an opaque OAuth bearer token by calling JHE's OIDC userinfo endpoint.

JHE (django-oauth-toolkit) issues opaque access tokens by default, not JWTs.
Calling `/o/userinfo/` with `Authorization: Bearer <opaque>` returns 200 + the
`sub` claim when the token is valid, and 401 otherwise — the standard OIDC
pattern for opaque-token validation.

We cache results for `cache_ttl` seconds so repeated MCP requests from the
same client don't hammer JHE.

Only a 401 is a verdict on the token; every other outcome is an `UpstreamAuthError`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from jhe_mcp.auth.upstream import UpstreamAuthError, upstream_status


class TokenValidationError(Exception):
    pass


@dataclass(frozen=True)
class _CachedSub:
    subject: str
    cached_at: float


class UserinfoValidator:
    def __init__(
        self,
        userinfo_endpoint: str,
        cache_ttl: int = 60,
        timeout: float = 5.0,
        max_entries: int = 1024,
    ) -> None:
        self._endpoint = userinfo_endpoint
        self._cache_ttl = cache_ttl
        self._timeout = timeout
        self._max_entries = max_entries
        self._cache: dict[str, _CachedSub] = {}

    def _evict(self, now: float) -> None:
        """Remove expired entries; if still over limit, drop the oldest."""
        expired = [k for k, v in self._cache.items() if now - v.cached_at >= self._cache_ttl]
        for k in expired:
            del self._cache[k]
        # If still at or over the limit, evict oldest by insertion order.
        while len(self._cache) >= self._max_entries:
            oldest = next(iter(self._cache))
            del self._cache[oldest]

    async def verify(self, token: str) -> str:
        """Return the `sub` claim if `token` is valid; raise on failure."""
        now = time.time()
        cached = self._cache.get(token)
        if cached is not None:
            if now - cached.cached_at < self._cache_ttl:
                return cached.subject
            # Expired entry — evict immediately.
            del self._cache[token]
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(self._endpoint, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise UpstreamAuthError(f"userinfo unreachable ({type(exc).__name__})") from exc
        if resp.status_code == 401:
            self._cache.pop(token, None)
            raise TokenValidationError("token rejected by userinfo endpoint")
        if resp.status_code != 200:
            raise UpstreamAuthError(f"userinfo returned {resp.status_code}", upstream_status(resp.status_code))
        try:
            body = resp.json()
        except ValueError as exc:
            raise UpstreamAuthError("userinfo returned a non-JSON body", 500) from exc
        sub = body.get("sub") if isinstance(body, dict) else None
        if not isinstance(sub, str) or not sub:
            raise UpstreamAuthError("userinfo response missing a usable 'sub' claim", 500)
        if len(self._cache) >= self._max_entries:
            self._evict(now)
        self._cache[token] = _CachedSub(subject=sub, cached_at=now)
        return sub
