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
   and warn once, rather than hard-failing.
"""

from __future__ import annotations

import logging

import httpx
from mcp.server.auth.provider import AccessToken, TokenVerifier

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
        # Layer 1: userinfo validation -> subject. Failure means reject.
        # A transport-level failure (httpx.HTTPError) must fail closed as a clean
        # 401 reject, not surface as a 500.
        try:
            subject = await self._validator.verify(token)
        except TokenValidationError:
            logger.warning("Userinfo rejected the token; rejecting request")
            return None
        except httpx.HTTPError as exc:
            # Fail closed, but this is an infra problem (JHE unreachable/timeout),
            # not a bad token — log at error with the cause so "auth server down"
            # is distinguishable from "unauthorized" in the logs.
            logger.error("Userinfo transport error (%s); rejecting token", type(exc).__name__)
            return None

        # Layer 2: best-effort audience check via introspection.
        client_id = await self._introspect_client_id(token)
        if client_id is None:
            # Introspection unavailable. Fail closed when MCP_REQUIRE_AUDIENCE is
            # set (production), else fall back to userinfo-only (dev default).
            if self._settings.require_audience:
                logger.warning("Audience required but introspection unavailable; rejecting token")
                return None
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
        can fall back to userinfo-only validation.
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
        except httpx.HTTPError:
            self._warn_audience_unenforced()
            return None
        if resp.status_code in (403, 404):
            # JHE does not expose introspection to us; audience can't be enforced.
            self._warn_audience_unenforced()
            return None
        if resp.status_code != 200:
            self._warn_audience_unenforced()
            return None
        try:
            body = resp.json()
        except ValueError:
            self._warn_audience_unenforced()
            return None
        if not body.get("active"):
            # Introspection says token is not active for us -> treat as foreign.
            return ""
        introspected = body.get("client_id")
        if not isinstance(introspected, str) or not introspected:
            self._warn_audience_unenforced()
            return None
        return introspected

    def _warn_audience_unenforced(self) -> None:
        if not self._audience_warning_emitted:
            self._audience_warning_emitted = True
            logger.warning(
                "JHE token introspection unavailable; token audience could not be "
                "enforced (falling back to userinfo-only validation)."
            )
