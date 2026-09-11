"""Report "JHE could not answer the token-validation call" as a 5xx, never a 401."""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

RETRY_AFTER_SECONDS = 30


class UpstreamAuthError(Exception):
    """JHE could not answer the token-validation call; the token is not known to be bad."""

    def __init__(self, detail: str, status_code: int = 503) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def upstream_status(status_code: int) -> int:
    """Map a JHE response status to 503 when a retry could succeed, else 500."""
    return 503 if status_code >= 500 or status_code in (408, 429) else 500


class UpstreamAuthErrorMiddleware:
    """Turn UpstreamAuthError raised during bearer auth into a JSON 5xx."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except UpstreamAuthError as exc:
            # Logged once here rather than per raise site; every raise reaches this handler.
            logger.error("Upstream auth failure (%s): %s", exc.status_code, exc.detail)
            transient = exc.status_code == 503
            # RFC 6749 §4.1.2.1 vocabulary, in the error-body shape the SDK uses for its 401s.
            body = {
                "error": "temporarily_unavailable" if transient else "server_error",
                "error_description": exc.detail,
            }
            headers = {"Retry-After": str(RETRY_AFTER_SECONDS)} if transient else None
            # Auth fails before any endpoint sends, so the response cannot have started.
            await JSONResponse(body, status_code=exc.status_code, headers=headers)(scope, receive, send)
