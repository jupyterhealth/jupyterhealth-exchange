"""Resource-server TokenVerifier for the MCP Streamable HTTP transport.

In resource-server mode the MCP SDK verifies the bearer token on *every*
request via this verifier and exposes the resulting AccessToken to tools through
``mcp.server.auth.middleware.auth_context.get_access_token``. This closes the
session-isolation hole that existed when auth was done once at session-init time
and snapshotted into a contextvar.

Validation has two layers:

1. ``UserinfoValidator`` confirms the token is live and yields its ``sub``.
2. (Best-effort) JHE token introspection confirms the token was issued to *our*
   JHE client, rejecting foreign-audience tokens. If JHE does not expose
   introspection (404 / 403 / connection error) we fall back to userinfo-only
   and warn once, rather than hard-failing — unless MCP_REQUIRE_AUDIENCE is set.

Either layer failing to reach a verdict raises ``UpstreamAuthError`` (a 5xx); only
a token JHE actually rejects returns None (a 401).
"""

from __future__ import annotations

import logging

import httpx
from mcp.server.auth.provider import AccessToken, TokenVerifier

from jhe_mcp.auth.upstream import UpstreamAuthError, upstream_status
from jhe_mcp.auth.userinfo import TokenValidationError, UserinfoValidator
from jhe_mcp.config import JHE_SCOPES, Settings

logger = logging.getLogger(__name__)


class JheAccessToken(AccessToken):
    """AccessToken that also carries the authenticated subject for audit use.

    Backward compatible with the SDK's AccessToken: ``subject`` is optional.
    """

    subject: str | None = None


class JheTokenVerifier(TokenVerifier):
    """Verify JHE-issued opaque bearer tokens for per-request MCP auth."""

    def __init__(
        self,
        settings: Settings,
        validator: UserinfoValidator | None = None,
        introspect_timeout: float = 5.0,
    ) -> None:
        self._settings = settings
        self._validator = validator or UserinfoValidator(userinfo_endpoint=settings.userinfo_endpoint)
        self._introspect_endpoint = f"{settings.jhe_base_url}/o/introspect/"
        self._introspect_timeout = introspect_timeout
        self._audience_warning_emitted = False

    async def verify_token(self, token: str) -> AccessToken | None:
        # Layer 1: userinfo validation -> subject. UpstreamAuthError propagates: unchecked != bad.
        try:
            subject = await self._validator.verify(token)
        except TokenValidationError:
            logger.warning("Userinfo rejected the token; rejecting request")
            return None

        # Layer 2: best-effort audience check via introspection.
        client_id = await self._introspect_client_id(token)
        if client_id is None:
            # Introspection unavailable and audience not required -> userinfo-only (dev default).
            client_id = self._settings.jhe_client_id
        elif client_id != self._settings.jhe_client_id:
            # Token was issued to a different client -> wrong audience. Reject.
            logger.warning("Rejecting token issued to foreign client_id")
            return None

        return JheAccessToken(
            token=token,
            client_id=client_id,
            scopes=list(JHE_SCOPES),
            subject=subject,
        )

    async def _introspect_client_id(self, token: str) -> str | None:
        """Return the introspected client_id, or None if introspection is unavailable.

        Returns the (possibly foreign) client_id when JHE reports the token as
        active; returns None when introspection cannot be performed so the caller
        can fall back to userinfo-only validation, or raises when MCP_REQUIRE_AUDIENCE is set.
        """
        auth: tuple[str, str] | None = None
        if self._settings.jhe_client_secret:
            auth = (self._settings.jhe_client_id, self._settings.jhe_client_secret)
        try:
            async with httpx.AsyncClient(timeout=self._introspect_timeout) as client:
                resp = await client.post(
                    self._introspect_endpoint,
                    data={"token": token},
                    auth=auth,
                )
        except httpx.HTTPError as exc:
            self._introspection_unavailable(503, f"introspection unreachable ({type(exc).__name__})")
            return None
        if resp.status_code != 200:
            status = upstream_status(resp.status_code)
            self._introspection_unavailable(status, f"introspection returned {resp.status_code}")
            return None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self._introspection_unavailable(500, "introspection returned a non-JSON object")
            return None
        if not body.get("active"):
            # Introspection says token is not active for us -> treat as foreign.
            return ""
        introspected = body.get("client_id")
        if not isinstance(introspected, str) or not introspected:
            self._introspection_unavailable(500, "introspection omitted client_id")
            return None
        return introspected

    def _introspection_unavailable(self, status_code: int, detail: str) -> None:
        """Raise when the audience must be enforced; otherwise warn once and fall back."""
        if self._settings.require_audience:
            raise UpstreamAuthError(detail, status_code=status_code)
        if not self._audience_warning_emitted:
            self._audience_warning_emitted = True
            logger.warning(
                "JHE token introspection unavailable; token audience could not be "
                "enforced (falling back to userinfo-only validation)."
            )
