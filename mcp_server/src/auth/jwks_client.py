"""
JWKS client for fetching and caching JHE OAuth server public keys

This module provides functionality to retrieve JSON Web Key Sets (JWKS)
from the JupyterHealth Exchange OAuth server for JWT signature verification.
"""

import logging
from typing import Optional
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from config import JHE_BASE_URL

logger = logging.getLogger(__name__)


class JHEJWKSClient:
    """
    Client for fetching and caching JWKS from JHE OAuth server

    Uses PyJWT's built-in PyJWKClient with caching to minimize
    network requests while ensuring up-to-date public keys.
    """

    def __init__(self):
        """
        Initialize JWKS client with JHE's JWKS endpoint

        The client will cache keys for 5 minutes (300 seconds) by default.
        Keys are automatically refreshed when a JWT contains an unknown 'kid'.
        """
        self.jwks_uri = f"{JHE_BASE_URL}/o/.well-known/jwks.json"

        logger.info(f"Initializing JWKS client with URI: {self.jwks_uri}")

        self.client = PyJWKClient(
            uri=self.jwks_uri,
            cache_keys=True,  # Cache individual keys
            cache_jwk_set=True,  # Cache entire JWKS
            lifespan=300,  # Cache for 5 minutes
            timeout=10,  # 10 second timeout for JWKS requests
        )

    def get_signing_key(self, token: str):
        """
        Get the public signing key for JWT verification

        Args:
            token: JWT token string (used to extract 'kid' from header)

        Returns:
            PyJWK object containing the public key

        Raises:
            PyJWKClientError: If JWKS fetch fails or kid not found

        Example:
            >>> client = JHEJWKSClient()
            >>> signing_key = client.get_signing_key(id_token)
            >>> jwt.decode(id_token, key=signing_key.key, ...)
        """
        try:
            signing_key = self.client.get_signing_key_from_jwt(token)
            logger.debug(f"Retrieved signing key with kid: {signing_key.key_id}")
            return signing_key
        except PyJWKClientError as e:
            logger.error(f"Failed to get signing key from JWKS: {e}")
            raise


# Global JWKS client instance (initialized lazily)
_jwks_client: Optional[JHEJWKSClient] = None


def get_jwks_client() -> JHEJWKSClient:
    """
    Get or create the global JWKS client instance

    Returns:
        JHEJWKSClient singleton instance
    """
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = JHEJWKSClient()
    return _jwks_client
