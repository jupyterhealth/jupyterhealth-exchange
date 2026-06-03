from __future__ import annotations

import asyncio
from typing import Any

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token

from jhe_mcp.audit import log_access
from jhe_mcp.auth.context import current_auth, current_auth_required


def assert_request_ctx_importable() -> None:
    """Fail fast if the per-request context import has moved.

    HTTP resource-server mode depends on ``mcp.server.lowlevel.server.request_ctx``
    to read the *current* request's token (see ``_per_request_bearer``). That is a
    non-public import path; if an ``mcp`` upgrade relocates it, ``_per_request_bearer``
    would silently swallow the ``ImportError`` and fall back to the initialize-time
    token — the exact session-isolation bug this server fixes, with no error. Call
    this at HTTP startup so a broken import path is a loud boot failure, not a quiet
    auth regression.
    """
    from mcp.server.lowlevel.server import request_ctx  # noqa: F401


def _per_request_bearer() -> str | None:
    """Return the bearer for the *current* MCP request, or None.

    In HTTP resource-server mode the SDK runs each session's tools inside a
    long-lived task started at ``initialize``; a plain contextvar (like
    ``get_access_token()``) therefore returns the *initialize-time* token, which
    is the original auth-isolation bug. The per-request principal is instead
    carried on the request context's ASGI ``Request`` (``scope["user"]``), set
    fresh by the SDK's auth middleware for every HTTP request. Read that first.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx

        ctx = request_ctx.get()
    except (ImportError, LookupError):
        ctx = None
    request = getattr(ctx, "request", None) if ctx is not None else None
    user = getattr(request, "user", None) if request is not None else None
    access_token = getattr(user, "access_token", None)
    if access_token is not None:
        return access_token.token
    # Fallback for non-HTTP / direct invocation: the contextvar set by the SDK's
    # AuthContextMiddleware (only correct when not crossing the session task).
    tok = get_access_token()
    return tok.token if tok is not None else None


def _per_request_subject() -> str | None:
    """Return the authenticated subject for the current request, or None.

    Mirrors ``_per_request_bearer``: prefer the per-request ``JheAccessToken``
    carried on the ASGI request context, then fall back to the SDK contextvar
    and finally the stdio AuthContext. Returns None when no subject is known.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx

        ctx = request_ctx.get()
    except (ImportError, LookupError):
        ctx = None
    request = getattr(ctx, "request", None) if ctx is not None else None
    user = getattr(request, "user", None) if request is not None else None
    access_token = getattr(user, "access_token", None)
    subject = getattr(access_token, "subject", None)
    if subject is not None:
        return subject
    tok = get_access_token()
    subject = getattr(tok, "subject", None)
    if subject is not None:
        return subject
    stdio_ctx = current_auth()
    return stdio_ctx.subject if stdio_ctx is not None else None


class JheClientError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"JHE {status}: {body}")
        self.status = status
        self.body = body


class JheClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> JheClient:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def admin_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        treat_404_as_none: bool = False,
    ) -> Any:
        return await self._get(f"/api/v1/{path.lstrip('/')}", params, treat_404_as_none)

    async def fhir_get(
        self,
        resource_path: str,
        params: dict[str, Any] | None = None,
        treat_404_as_none: bool = False,
    ) -> Any:
        return await self._get(f"/fhir/r5/{resource_path.lstrip('/')}", params, treat_404_as_none)

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None,
        treat_404_as_none: bool,
    ) -> Any:
        if self._client is None:
            raise RuntimeError("JheClient must be used as async context manager")
        # Prefer the SDK's per-request token (HTTP resource-server mode): it is
        # verified per request and reflects the *current* caller, not whoever
        # initialized the session. Fall back to the stdio contextvar.
        bearer = _per_request_bearer()
        if bearer is None:
            bearer = current_auth_required().bearer_token
        subject = _per_request_subject()
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {bearer}"}
        # One retry on transport errors or 5xx; everything else returns/raises immediately.
        for is_retry in (False, True):
            try:
                resp = await self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                if is_retry:
                    log_access(subject=subject, method="GET", path=path, status=0)
                    raise JheClientError(0, str(exc)) from exc
                await asyncio.sleep(0.5)
                continue
            if resp.status_code == 404 and treat_404_as_none:
                log_access(subject=subject, method="GET", path=path, status=resp.status_code)
                return None
            if (resp.status_code == 429 or 500 <= resp.status_code < 600) and not is_retry:
                await asyncio.sleep(0.5)
                continue
            log_access(subject=subject, method="GET", path=path, status=resp.status_code)
            if resp.status_code >= 400:
                raise JheClientError(resp.status_code, resp.text)
            return resp.json()
        raise AssertionError("unreachable: retry loop must return or raise")  # pragma: no cover
