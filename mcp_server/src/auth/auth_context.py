"""
Authentication context and permission management

This module reads user permissions from OAuth ID token claims instead of
making API calls to JHE. This eliminates network overhead and enables
offline operation.
"""

from typing import Optional, List, Dict
import jwt


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
        """
        try:
            # Decode ID token without signature verification (we trust the source)
            # Signature was already verified by OAuth server
            claims = jwt.decode(id_token, options={"verify_signature": False})

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

            print("✓ Loaded permissions from ID token:")
            print(f"  User: {self.user_id} ({self.user_type})")
            print(f"  Studies: {len(self.study_ids)} accessible")
            print(f"  Organizations: {len(self.roles_by_org)} memberships")

        except Exception as e:
            print(f"⚠️  Could not extract claims from ID token: {e}")
            print("   User will have no permissions until re-authentication")
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
