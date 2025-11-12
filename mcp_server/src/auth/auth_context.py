"""
Authentication context and permission management

This module reads user permissions from OAuth ID token claims instead of
making API calls to JHE. This eliminates network overhead and enables
offline operation.
"""

import logging
from typing import Optional, List, Dict
import jwt
from jwt.exceptions import (
    PyJWTError,
    ExpiredSignatureError,
    InvalidSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
)

from config import CLIENT_ID, JHE_OIDC_ISSUER, SKIP_JWT_VERIFICATION
from .jwks_client import get_jwks_client

logger = logging.getLogger(__name__)


class AuthContext:
    """Validates token and manages user permissions from ID token claims"""

    def __init__(self, token: str, id_token: Optional[str] = None):
        """
        Initialize auth context from OAuth tokens.

        Args:
            token: Access token (opaque, used for API authentication)
            id_token: ID token (JWT with custom claims containing permissions)

        If id_token is provided and contains custom claims, permissions are
        extracted locally without API calls. Otherwise, falls back to empty
        permissions (user can still authenticate but with no access).
        """
        self.token = token
        self.user_id: Optional[int] = None
        self.email: Optional[str] = None
        self.user_type: Optional[str] = None  # 'patient' or 'practitioner'
        self.is_superuser: bool = False
        self.roles_by_org: Dict[int, str] = {}  # {org_id: role}
        self.study_ids: List[int] = []

        # Extract permissions from ID token if available
        if id_token:
            self._extract_claims_from_id_token(id_token)

    def _extract_claims_from_id_token(self, id_token: str):
        """
        Extract user information and permissions from ID token claims.

        The ID token contains custom claims added by JHE's JHEOAuth2Validator:
        - user_type: "patient" or "practitioner"
        - user_id: JheUser primary key
        - jhe_permissions: Object with studies and organizations

        Args:
            id_token: JWT ID token from OAuth flow

        Raises:
            InvalidSignatureError: If JWT signature verification fails (token forged)
            ExpiredSignatureError: If JWT has expired
            InvalidAudienceError: If JWT audience doesn't match CLIENT_ID
            InvalidIssuerError: If JWT issuer doesn't match JHE
            PyJWTError: For other JWT-related errors
        """
        try:
            # Verify JWT signature using JHE's public keys
            if SKIP_JWT_VERIFICATION:
                logger.warning("⚠️  JWT SIGNATURE VERIFICATION DISABLED - INSECURE!")
                logger.warning("   This should ONLY be used in test environments")
                claims = jwt.decode(id_token, options={"verify_signature": False})
            else:
                # Get signing key from JWKS endpoint
                jwks_client = get_jwks_client()
                signing_key = jwks_client.get_signing_key(id_token)

                # Verify signature and standard claims
                claims = jwt.decode(
                    id_token,
                    key=signing_key.key,
                    algorithms=["RS256"],
                    audience=CLIENT_ID,
                    issuer=JHE_OIDC_ISSUER,
                    options={
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_aud": True,
                        "verify_iss": True,
                    },
                )
                logger.info("✓ JWT signature verified successfully")

            # Extract standard OIDC claims
            self.user_id = claims.get("user_id") or claims.get("sub")
            self.email = claims.get("email")

            # Extract custom claims
            self.user_type = claims.get("user_type")

            # Check if user is superuser (special user_type or is_superuser claim)
            self.is_superuser = self.user_type == "superuser" or claims.get("is_superuser", False)

            # Extract permissions from jhe_permissions custom claim
            permissions = claims.get("jhe_permissions", {})

            # Get accessible studies
            self.study_ids = permissions.get("studies", [])

            # Get organizations with roles
            organizations = permissions.get("organizations", [])
            for org in organizations:
                org_id = org.get("id")
                role = org.get("role")
                if org_id and role:
                    self.roles_by_org[org_id] = role

            logger.info("✓ Loaded permissions from ID token:")
            logger.info(f"  User: {self.user_id} ({self.user_type})")
            logger.info(f"  Studies: {len(self.study_ids)} accessible")
            logger.info(f"  Organizations: {len(self.roles_by_org)} memberships")

        except ExpiredSignatureError:
            logger.error("❌ ID token has expired - re-authentication required")
            raise PermissionError("ID token expired. Please re-authenticate.")
        except InvalidSignatureError:
            logger.error("❌ ID token signature verification failed - possible forged token")
            raise PermissionError("Invalid ID token signature. Authentication failed.")
        except InvalidAudienceError:
            logger.error(f"❌ ID token audience mismatch - expected {CLIENT_ID}")
            raise PermissionError("ID token audience mismatch. Authentication failed.")
        except InvalidIssuerError:
            logger.error(f"❌ ID token issuer mismatch - expected {JHE_OIDC_ISSUER}")
            raise PermissionError("ID token issuer mismatch. Authentication failed.")
        except PyJWTError as e:
            logger.error(f"❌ JWT verification error: {e}")
            raise PermissionError(f"JWT verification failed: {str(e)}")
        except Exception as e:
            logger.error(f"⚠️  Could not extract claims from ID token: {e}", exc_info=True)
            logger.warning("   User will have no permissions until re-authentication")
            # Leave all permission fields as empty defaults

    def validate(self) -> bool:
        """
        Validate that authentication context is ready.

        With ID token claims, validation is just checking that we have user info.
        The access token itself is validated by JHE API when making requests.

        Returns:
            True if context has user information

        Raises:
            PermissionError: If no user information available
        """
        if not self.user_id:
            raise PermissionError(
                "No user information available. " "ID token may be missing or invalid. " "Try re-authenticating."
            )

        return True

    def can_access_study(self, study_id: int) -> bool:
        """
        Check if user has access to specific study

        Args:
            study_id: Study identifier

        Returns:
            True if user can access the study
        """
        if self.is_superuser:
            return True

        return study_id in self.study_ids

    def __repr__(self) -> str:
        return (
            f"AuthContext(user_id={self.user_id}, "
            f"email={self.email}, "
            f"user_type={self.user_type}, "
            f"is_superuser={self.is_superuser}, "
            f"studies={len(self.study_ids)}, "
            f"orgs={len(self.roles_by_org)})"
        )
