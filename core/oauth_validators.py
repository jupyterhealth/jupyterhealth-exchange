"""
Custom OAuth2 Validator for JupyterHealth Exchange
Adds user permissions to OIDC ID tokens
"""
import logging
from oauth2_provider.oauth2_validators import OAuth2Validator
from core.models import Study, PractitionerOrganization


logger = logging.getLogger(__name__)


class JHEOAuth2Validator(OAuth2Validator):
    """
    Custom validator that adds JHE-specific claims to ID tokens.

    This validator extends the default OAuth2Validator to include user permissions
    (accessible studies and organizations) directly in the OIDC ID token. This
    enables MCP servers and other clients to make authorization decisions locally
    without requiring additional API calls to /userinfo endpoints.
    """

    # Map custom claims to the 'openid' scope so they are included in ID tokens
    # Without this, Django OAuth Toolkit 2.0+ will filter out custom claims
    oidc_claim_scope = None  # Disable scope filtering (include all custom claims)

    def get_additional_claims(self, request):
        """
        Add custom claims to the ID token.

        This method is called during token generation and adds:
        - user_type: "patient" or "practitioner"
        - user_id: JheUser primary key
        - jhe_permissions: Object containing accessible studies and organizations

        Args:
            request: OAuthlib request object with authenticated user

        Returns:
            dict: Custom claims to add to ID token

        Example ID token payload:
            {
                "iss": "https://jhe.fly.dev/o",
                "sub": "20001",
                "aud": "...",
                "exp": 1729283400,
                "iat": 1729197000,
                "auth_time": 1729197000,
                "user_type": "practitioner",
                "user_id": 20001,
                "jhe_permissions": {
                    "studies": [30001, 30002, 30003],
                    "organizations": [
                        {"id": 50001, "name": "BIDS", "role": "manager"},
                        {"id": 50002, "name": "Cardiology", "role": "member"}
                    ]
                }
            }
        """
        user = request.user

        # For patient users, only include basic user info (no org/study access)
        if user.user_type == 'patient':
            return {
                'user_type': 'patient',
                'user_id': user.id,
            }

        # For practitioners and other user types, fetch accessible studies and organizations
        try:
            # Get all studies accessible via practitioner's organizations
            # Uses the related_name 'practitioner_links' from PractitionerOrganization model
            # Query: Studies linked to orgs that have practitioner links to this user
            accessible_studies = list(
                Study.objects.filter(
                    organization__practitioner_links__practitioner__jhe_user=user
                ).values_list('id', flat=True).distinct()
            )

            # Get practitioner's organizations with roles
            # select_related() optimizes query by joining organization table
            practitioner_orgs = list(
                PractitionerOrganization.objects.filter(
                    practitioner__jhe_user=user
                ).select_related('organization').values(
                    'organization_id',
                    'organization__name',
                    'role'
                )
            )

            # Transform database results into clean JSON structure
            organizations = [
                {
                    'id': org['organization_id'],
                    'name': org['organization__name'],
                    'role': org['role']
                }
                for org in practitioner_orgs
            ]

        except Exception as e:
            # Log error but don't fail token generation
            # This ensures OAuth flow continues even if permission lookup fails
            logger.error(
                f"Error fetching permissions for user {user.id} ({user.email}): {e}",
                exc_info=True
            )
            accessible_studies = []
            organizations = []

        return {
            'user_type': user.user_type,
            'user_id': user.id,
            'jhe_permissions': {
                'studies': accessible_studies,
                'organizations': organizations,
            }
        }
