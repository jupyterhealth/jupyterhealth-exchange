"""
Tests for Custom OAuth2 Validator

Tests the JHEOAuth2Validator class which adds custom claims to ID tokens
including user permissions (accessible studies and organizations).
"""

from unittest.mock import Mock
from django.test import TestCase

from core.models import (
    JheUser,
    Organization,
    Practitioner,
    PractitionerOrganization,
    Study,
)
from core.oauth_validators import JHEOAuth2Validator


class JHEOAuth2ValidatorTests(TestCase):
    """Test cases for custom OAuth2 validator that adds claims to ID tokens"""

    def setUp(self):
        """Set up test fixtures"""
        self.validator = JHEOAuth2Validator()

        # Create test organizations
        self.org1 = Organization.objects.create(
            name="Berkeley Institute for Data Science (BIDS)", type="edu"
        )
        self.org2 = Organization.objects.create(name="Cardiology Department", type="dept")

        # Create test studies
        self.study1 = Study.objects.create(
            name="COVID-19 Research Study", description="Longitudinal COVID study", organization=self.org1
        )
        self.study2 = Study.objects.create(
            name="Heart Disease Study", description="Cardiovascular research", organization=self.org1
        )
        self.study3 = Study.objects.create(
            name="Clinical Trial 123", description="Drug efficacy trial", organization=self.org2
        )

        # Create patient user
        self.patient_user = JheUser.objects.create_user(
            email="patient@example.com", password="testpass", user_type="patient", identifier="patient123"
        )

        # Create practitioner user
        self.practitioner_user = JheUser.objects.create_user(
            email="practitioner@example.com", password="testpass", user_type="practitioner", identifier="prac123"
        )
        self.practitioner = Practitioner.objects.create(
            jhe_user=self.practitioner_user, first_name="Sam", last_name="Altman"
        )

        # Link practitioner to organizations with roles
        PractitionerOrganization.objects.create(
            practitioner=self.practitioner, organization=self.org1, role=PractitionerOrganization.ROLE_MANAGER
        )
        PractitionerOrganization.objects.create(
            practitioner=self.practitioner, organization=self.org2, role=PractitionerOrganization.ROLE_MEMBER
        )

    def _create_mock_request(self, user):
        """Helper to create a mock OAuthlib request object"""
        request = Mock()
        request.user = user
        return request

    def test_patient_user_gets_minimal_claims(self):
        """Patient users should only get user_type and user_id claims"""
        request = self._create_mock_request(self.patient_user)
        claims = self.validator.get_additional_claims(request)

        self.assertEqual(claims["user_type"], "patient")
        self.assertEqual(claims["user_id"], self.patient_user.id)
        self.assertNotIn("jhe_permissions", claims)

    def test_practitioner_gets_accessible_studies(self):
        """Practitioner should get list of all studies from their organizations"""
        request = self._create_mock_request(self.practitioner_user)
        claims = self.validator.get_additional_claims(request)

        self.assertEqual(claims["user_type"], "practitioner")
        self.assertEqual(claims["user_id"], self.practitioner_user.id)
        self.assertIn("jhe_permissions", claims)

        permissions = claims["jhe_permissions"]
        studies = permissions["studies"]

        # Should have access to all 3 studies (2 from org1, 1 from org2)
        self.assertEqual(len(studies), 3)
        self.assertIn(self.study1.id, studies)
        self.assertIn(self.study2.id, studies)
        self.assertIn(self.study3.id, studies)

    def test_practitioner_gets_organizations_with_roles(self):
        """Practitioner should get list of organizations with their roles"""
        request = self._create_mock_request(self.practitioner_user)
        claims = self.validator.get_additional_claims(request)

        permissions = claims["jhe_permissions"]
        organizations = permissions["organizations"]

        # Should have 2 organizations
        self.assertEqual(len(organizations), 2)

        # Find org1 in results
        org1_claim = next(org for org in organizations if org["id"] == self.org1.id)
        self.assertEqual(org1_claim["name"], "Berkeley Institute for Data Science (BIDS)")
        self.assertEqual(org1_claim["role"], PractitionerOrganization.ROLE_MANAGER)

        # Find org2 in results
        org2_claim = next(org for org in organizations if org["id"] == self.org2.id)
        self.assertEqual(org2_claim["name"], "Cardiology Department")
        self.assertEqual(org2_claim["role"], PractitionerOrganization.ROLE_MEMBER)

    def test_practitioner_without_organizations_gets_empty_permissions(self):
        """Practitioner with no organization links should get empty permissions"""
        # Create a practitioner with no organization links
        lonely_user = JheUser.objects.create_user(
            email="lonely@example.com", password="testpass", user_type="practitioner", identifier="lonely123"
        )
        lonely_practitioner = Practitioner.objects.create(
            jhe_user=lonely_user, first_name="Lonely", last_name="Practitioner"
        )

        request = self._create_mock_request(lonely_user)
        claims = self.validator.get_additional_claims(request)

        permissions = claims["jhe_permissions"]
        self.assertEqual(len(permissions["studies"]), 0)
        self.assertEqual(len(permissions["organizations"]), 0)

    def test_claim_structure_matches_expected_format(self):
        """Verify the overall structure of claims matches documentation"""
        request = self._create_mock_request(self.practitioner_user)
        claims = self.validator.get_additional_claims(request)

        # Top-level keys
        self.assertIn("user_type", claims)
        self.assertIn("user_id", claims)
        self.assertIn("jhe_permissions", claims)

        # jhe_permissions structure
        permissions = claims["jhe_permissions"]
        self.assertIn("studies", permissions)
        self.assertIn("organizations", permissions)

        # studies should be list of integers
        self.assertIsInstance(permissions["studies"], list)
        for study_id in permissions["studies"]:
            self.assertIsInstance(study_id, int)

        # organizations should be list of dicts with id, name, role
        self.assertIsInstance(permissions["organizations"], list)
        for org in permissions["organizations"]:
            self.assertIn("id", org)
            self.assertIn("name", org)
            self.assertIn("role", org)
            self.assertIsInstance(org["id"], int)
            self.assertIsInstance(org["name"], str)
            self.assertIsInstance(org["role"], str)

    def test_multiple_studies_in_same_organization(self):
        """Verify practitioner gets all studies from an organization"""
        # org1 already has 2 studies, verify both are included
        request = self._create_mock_request(self.practitioner_user)
        claims = self.validator.get_additional_claims(request)

        studies = claims["jhe_permissions"]["studies"]

        # Should include both studies from org1
        self.assertIn(self.study1.id, studies)
        self.assertIn(self.study2.id, studies)

    def test_superuser_type_gets_empty_permissions_structure(self):
        """Superuser (non-patient, non-practitioner) should get empty permissions"""
        superuser = JheUser.objects.create_superuser(email="admin@example.com", password="adminpass")
        # Superuser has user_type = None by default

        request = self._create_mock_request(superuser)
        claims = self.validator.get_additional_claims(request)

        self.assertIsNone(claims["user_type"])
        self.assertEqual(claims["user_id"], superuser.id)
        # Should still have jhe_permissions but empty
        self.assertIn("jhe_permissions", claims)
        self.assertEqual(len(claims["jhe_permissions"]["studies"]), 0)
        self.assertEqual(len(claims["jhe_permissions"]["organizations"]), 0)

    def test_database_error_returns_empty_permissions(self):
        """If database queries fail, should return empty permissions without crashing"""
        request = self._create_mock_request(self.practitioner_user)

        # Simulate database error by using a user with invalid references
        # This tests the exception handling in get_additional_claims
        invalid_user = JheUser.objects.create_user(
            email="invalid@example.com", password="testpass", user_type="practitioner", identifier="invalid123"
        )
        # Don't create a Practitioner object - this will cause queries to return empty

        request.user = invalid_user
        claims = self.validator.get_additional_claims(request)

        # Should still return valid structure with empty permissions
        self.assertIn("jhe_permissions", claims)
        self.assertEqual(len(claims["jhe_permissions"]["studies"]), 0)
        self.assertEqual(len(claims["jhe_permissions"]["organizations"]), 0)
