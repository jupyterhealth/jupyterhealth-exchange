"""
Secure token storage and retrieval
"""

import json
import time
import secrets
import jwt
from typing import Optional
import requests

from config import TOKEN_CACHE_PATH, JHE_TOKEN_URL, CLIENT_ID, CLIENT_SECRET, OIDC_RSA_PRIVATE_KEY


class TokenCache:
    """Manages secure token storage and retrieval"""

    @staticmethod
    def save_token(token_data: dict):
        """
        Save token to local cache with restricted permissions

        Args:
            token_data: Token response from OAuth server
        """
        # Calculate expiry time
        if "expires_in" in token_data:
            token_data["expires_at"] = time.time() + token_data["expires_in"]

        # Write to file
        with open(TOKEN_CACHE_PATH, "w") as f:
            json.dump(token_data, f, indent=2)

        # Set restrictive permissions (owner read/write only)
        TOKEN_CACHE_PATH.chmod(0o600)

    @staticmethod
    def load_token() -> Optional[dict]:
        """
        Load token from cache if exists and valid

        Returns:
            Token data if valid, None otherwise
        """
        if not TOKEN_CACHE_PATH.exists():
            return None

        try:
            with open(TOKEN_CACHE_PATH, "r") as f:
                token_data = json.load(f)

            # Check if token is expired
            if "expires_at" in token_data:
                if time.time() >= token_data["expires_at"]:
                    # Token expired, try refresh
                    return TokenCache.refresh_token(token_data)

            return token_data
        except Exception as e:
            print(f"Error loading token: {e}")
            return None

    @staticmethod
    def _create_client_assertion() -> Optional[str]:
        """Create JWT client assertion for private_key_jwt"""
        if not OIDC_RSA_PRIVATE_KEY:
            return None

        now = int(time.time())
        claims = {
            "iss": CLIENT_ID,
            "sub": CLIENT_ID,
            "aud": JHE_TOKEN_URL,
            "jti": secrets.token_urlsafe(16),
            "exp": now + 300,
            "iat": now,
        }

        try:
            return jwt.encode(claims, OIDC_RSA_PRIVATE_KEY, algorithm="RS256")
        except Exception as e:
            print(f"Error creating client assertion: {e}")
            return None

    @staticmethod
    def refresh_token(token_data: dict) -> Optional[dict]:
        """
        Refresh expired access token using refresh token

        Args:
            token_data: Existing token data with refresh_token

        Returns:
            New token data if successful, None otherwise
        """
        if "refresh_token" not in token_data:
            print("No refresh token available")
            return None

        try:
            refresh_data = {
                "grant_type": "refresh_token",
                "refresh_token": token_data["refresh_token"],
                "client_id": CLIENT_ID,
            }

            # Use either private_key_jwt or client_secret
            if OIDC_RSA_PRIVATE_KEY:
                client_assertion = TokenCache._create_client_assertion()
                if not client_assertion:
                    return None
                refresh_data["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                refresh_data["client_assertion"] = client_assertion
            elif CLIENT_SECRET:
                refresh_data["client_secret"] = CLIENT_SECRET

            response = requests.post(JHE_TOKEN_URL, data=refresh_data, timeout=10)

            if response.status_code == 200:
                new_token = response.json()
                # Preserve refresh_token if not returned
                if "refresh_token" not in new_token and "refresh_token" in token_data:
                    new_token["refresh_token"] = token_data["refresh_token"]

                TokenCache.save_token(new_token)
                print("✓ Token refreshed successfully")
                return new_token
            else:
                print(f"Token refresh failed: {response.status_code}")
                return None
        except Exception as e:
            print(f"Error refreshing token: {e}")
            return None

    @staticmethod
    def clear_token():
        """Remove cached token"""
        if TOKEN_CACHE_PATH.exists():
            TOKEN_CACHE_PATH.unlink()
            print("Token cache cleared")

    @staticmethod
    def get_valid_token() -> Optional[str]:
        """
        Get a valid access token, refreshing if necessary

        Returns:
            Access token string if valid, None otherwise
        """
        token_data = TokenCache.load_token()

        if token_data and "access_token" in token_data:
            return token_data["access_token"]

        return None

    @staticmethod
    def get_valid_tokens() -> Optional[tuple[str, Optional[str]]]:
        """
        Get valid access token and ID token, refreshing if necessary

        Returns:
            Tuple of (access_token, id_token) if valid, None otherwise
            id_token may be None if not present in token response
        """
        token_data = TokenCache.load_token()

        if token_data and "access_token" in token_data:
            access_token = token_data["access_token"]
            id_token = token_data.get("id_token")  # May be None
            return (access_token, id_token)

        return None
