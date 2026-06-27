"""Verify EHR-issued OpenID Connect id_tokens for SMART on FHIR token exchange.

Uses PyJWT's PyJWKClient to validate the id_token signature against the EHR's
JWKS (discovered from .well-known/smart-configuration). Relies only on
ONC g(10)-mandated capabilities, so the same path works across EHR vendors.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import jwt
import requests

logger = logging.getLogger(__name__)

# Asymmetric only. Never 'none'; never HS* (algorithm-confusion).
ALLOWED_ALGS = ["RS256", "RS384", "ES384"]

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
            jwks_uri = r.json().get("jwks_uri")
            if jwks_uri:
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
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWKClientError as e:
        raise IdTokenError(f"Could not resolve signing key: {e}", status_code=502) from e
    except jwt.InvalidTokenError as e:
        logger.warning("id_token validation failed: %s", e)
        raise IdTokenError("id_token failed validation", status_code=401) from e


def parse_fhir_user(fhir_user: str) -> tuple[str, str]:
    """Split a fhirUser reference (relative or absolute) into (resource_type, id)."""
    parts = fhir_user.rstrip("/").split("/")
    if len(parts) < 2 or not parts[-1] or not parts[-2]:
        raise IdTokenError(f"Malformed fhirUser reference: {fhir_user!r}", status_code=400)
    return parts[-2], parts[-1]
