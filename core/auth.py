"""Everything auth-related for JHE, in one place.

Five concerns that all answer "how does a caller prove who they are, and where
do they go to do it", kept together so there is a single file to open rather
than five one-class modules:

1. Account activation tokens -- emailed signup/verification links.
2. DRF authentication       -- resolves a user for client-credentials tokens.
3. DOT OIDC validator       -- adds the email claim to /o/userinfo/.
4. Advertised endpoint URIs -- what the FHIR discovery documents publish.
5. EHR id_token verification -- inbound SMART on FHIR token exchange.

Referenced by dotted path from settings (``DEFAULT_AUTHENTICATION_CLASSES``,
``OAUTH2_VALIDATOR_CLASS``), so renaming this module means editing those too.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import jwt
import requests
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.urls import reverse
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from oauth2_provider.oauth2_validators import OAuth2Validator
from six import text_type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Account activation tokens
# ---------------------------------------------------------------------------


class TokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        return text_type(user.id) + text_type(timestamp) + text_type(user.email_is_verified)


account_activation_token = TokenGenerator()


# ---------------------------------------------------------------------------
# 2. DRF authentication
# ---------------------------------------------------------------------------


class JheOAuth2Authentication(OAuth2Authentication):
    """Resolve a user for client-credentials tokens too.

    Authorization-code (and password/refresh) tokens carry the resource owner directly on the
    token, so DRF exposes it as ``request.user``. Client-credentials tokens have no resource
    owner -- django-oauth-toolkit saves them with ``user=None`` -- so DRF would otherwise treat
    the request as unauthenticated. For those, fall back to the application owner
    (``token.application.user``); for a PractitionerClient that is the practitioner who created
    it. This keeps ``request.user`` correct everywhere without per-view changes.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        if user is None and token is not None:
            application = getattr(token, "application", None)
            if application is not None and application.user is not None:
                user = application.user
        return user, token


# ---------------------------------------------------------------------------
# 3. DOT OIDC validator
# ---------------------------------------------------------------------------


class JheOAuth2Validator(OAuth2Validator):
    """Extend DOT's default validator so /o/userinfo/ returns the email claim
    when the 'email' scope is granted.

    DOT's built-in ``oidc_claim_scope`` already maps ``"email" -> "email"``
    so we only need to supply the actual claim value here.
    """

    def get_additional_claims(self, request):
        return {
            "email": request.user.email,
        }


# ---------------------------------------------------------------------------
# 4. Advertised OAuth endpoint URIs
# ---------------------------------------------------------------------------
# Django OAuth Toolkit has no setting for its own mount point -- ``/o/`` is a
# ``jhe/urls.py`` decision (see ``settings.OAUTH_MOUNT_PATH``). Anything that
# *advertises* the authorize or token endpoint must therefore reverse it rather
# than hardcode the path: both FHIR discovery documents publish the same two
# endpoints, so a literal in each would let them drift apart, or away from the
# routes actually served, the moment the mount point moved. Reversing also
# survives a deployment mounted below the site root, where a hardcoded absolute
# path would silently drop the script prefix.


def authorize_uri(request):
    """The absolute OAuth authorize URI for this request's scheme and host."""
    return request.build_absolute_uri(reverse("oauth2_provider:authorize"))


def token_uri(request):
    """The absolute OAuth token URI for this request's scheme and host."""
    return request.build_absolute_uri(reverse("oauth2_provider:token"))


# ---------------------------------------------------------------------------
# 5. EHR id_token verification (SMART on FHIR token exchange)
# ---------------------------------------------------------------------------
# Uses PyJWT's PyJWKClient to validate the id_token signature against the EHR's
# JWKS (discovered from .well-known/smart-configuration). Relies only on
# ONC g(10)-mandated capabilities, so the same path works across EHR vendors.

# Asymmetric only. Never 'none'; never HS* (algorithm-confusion).
ALLOWED_ALGS = ["RS256", "RS384", "ES384"]

# Tolerance for EHR<->JHE clock drift on exp/iat/nbf (seconds).
LEEWAY_SECONDS = 30

_DISCOVERY_PATHS = (".well-known/smart-configuration", ".well-known/openid-configuration")


class IdTokenError(Exception):
    """An id_token could not be verified. ``status_code`` is the HTTP status to return."""

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


def discover_jwks_uri(issuer: str) -> str:
    """Return the issuer's jwks_uri via SMART/OIDC discovery."""
    base = issuer.rstrip("/")
    for path in _DISCOVERY_PATHS:
        url = f"{base}/{path}"
        try:
            r = requests.get(url, headers={"Accept": "application/json"}, timeout=10)
        except requests.RequestException as e:
            logger.warning("Discovery request failed for %s: %s", url, e)
            continue
        if r.ok:
            try:
                jwks_uri = r.json().get("jwks_uri")
            except ValueError:
                logger.warning("Discovery doc at %s was not valid JSON", url)
                continue
            if jwks_uri and jwks_uri.startswith("https://"):
                return jwks_uri
    raise IdTokenError(f"Could not discover jwks_uri for issuer {issuer!r}", status_code=502)


@lru_cache(maxsize=32)
def _jwk_client(jwks_uri: str) -> jwt.PyJWKClient:
    # PyJWKClient caches keys internally; lru_cache reuses the client per URI.
    return jwt.PyJWKClient(jwks_uri)


def verify_id_token(id_token: str, *, issuer: str, audience: str) -> dict:
    """Verify an EHR id_token and return its claims, or raise IdTokenError."""
    jwks_uri = discover_jwks_uri(issuer)
    try:
        signing_key = _jwk_client(jwks_uri).get_signing_key_from_jwt(id_token)
        return jwt.decode(
            id_token,
            signing_key.key,
            algorithms=ALLOWED_ALGS,
            audience=audience,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWKClientError as e:
        logger.warning("Could not resolve signing key: %s", e)
        raise IdTokenError("Could not resolve signing key", status_code=502) from e
    except jwt.InvalidTokenError as e:
        logger.warning("id_token validation failed: %s", e)
        raise IdTokenError("id_token failed validation", status_code=401) from e


def parse_fhir_user(fhir_user: str) -> tuple[str, str]:
    """Split a fhirUser reference (relative or absolute) into (resource_type, id)."""
    parts = fhir_user.rstrip("/").split("/")
    if len(parts) < 2 or not parts[-1] or not parts[-2]:
        raise IdTokenError(f"Malformed fhirUser reference: {fhir_user!r}", status_code=400)
    return parts[-2], parts[-1]
