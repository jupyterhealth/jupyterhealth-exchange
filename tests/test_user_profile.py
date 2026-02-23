"""
Tests for GET /api/v1/users/profile

Validates that the profile endpoint returns appropriate data based on user type,
ensuring that Protected Health Information (PHI) is not exposed to patient users.

Test categories:
  - Unit:        Serializer-level field inclusion/exclusion
  - Integration: Full HTTP round-trip through the DRF view
  - Regression:  Practitioner and superuser profiles remain unchanged
  - Accuracy:    Exact field sets and value correctness
"""

import pytest
from rest_framework.test import APIClient

from core.models import JheUser
from core.serializers import (
    JheUserPatientProfileSerializer,
    JheUserSerializer,
    PatientProfileSerializer,
    PatientSerializer,
)

PROFILE_URL = "/api/v1/users/profile"

# ── Field-set constants ──────────────────────────────────────────────────────

# Top-level fields that MUST be absent for patient users
PATIENT_USER_REDACTED_FIELDS = {"email", "firstName", "lastName"}

# Nested patient.* fields that MUST be absent for patient users
PATIENT_NESTED_REDACTED_FIELDS = {
    "nameFamily",
    "nameGiven",
    "birthDate",
    "telecomPhone",
    "telecomEmail",
}

# Top-level fields expected for patient users
PATIENT_USER_EXPECTED_FIELDS = {"id", "patient", "userType", "isSuperuser"}

# Nested patient fields expected for patient users
PATIENT_NESTED_EXPECTED_FIELDS = {"id", "jheUserId", "identifier", "organizations"}

# Full field set for practitioners (unchanged behaviour)
PRACTITIONER_USER_EXPECTED_FIELDS = {
    "id",
    "email",
    "firstName",
    "lastName",
    "patient",
    "userType",
    "isSuperuser",
}


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def patient_client(patient):
    """APIClient authenticated as the patient user from conftest."""
    client = APIClient()
    client.force_authenticate(patient.jhe_user)
    return client


@pytest.fixture
def practitioner_client(user):
    """APIClient authenticated as the practitioner user from conftest."""
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def superuser_client(superuser):
    """APIClient authenticated as a superuser."""
    client = APIClient()
    client.force_authenticate(superuser)
    return client


@pytest.fixture
def patient_with_details(organization):
    """A patient whose PHI fields are explicitly populated for accuracy tests."""
    user = JheUser.objects.create_user(
        email="phi-patient@example.org",
        password="testpass123",
        first_name="Alice",
        last_name="Smith",
        identifier="phi-test-patient",
        user_type="patient",
    )
    pat = user.patient_profile
    pat.name_family = "Smith"
    pat.name_given = "Alice"
    pat.birth_date = "1990-05-15"
    pat.telecom_phone = "+1-555-000-1234"
    pat.telecom_email = "alice.smith@example.org"
    pat.save()
    pat.organizations.add(organization)
    return pat


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — serializer-level
# ═══════════════════════════════════════════════════════════════════════════


class TestPatientProfileSerializerUnit:
    """Verify PatientProfileSerializer excludes PHI fields at the serializer level."""

    @pytest.mark.django_db
    def test_fields_list_has_no_phi(self):
        declared = set(PatientProfileSerializer.Meta.fields)
        for phi_field in ("name_family", "name_given", "birth_date", "telecom_phone", "telecom_email"):
            assert phi_field not in declared

    @pytest.mark.django_db
    def test_fields_list_includes_expected(self):
        declared = set(PatientProfileSerializer.Meta.fields)
        for expected in ("id", "jhe_user_id", "identifier", "organizations"):
            assert expected in declared

    @pytest.mark.django_db
    def test_serialized_output_excludes_phi(self, patient_with_details):
        data = PatientProfileSerializer(patient_with_details).data
        for key in PATIENT_NESTED_REDACTED_FIELDS:
            assert key not in data, f"PHI key '{key}' leaked in PatientProfileSerializer output"

    @pytest.mark.django_db
    def test_serialized_output_includes_expected(self, patient_with_details):
        data = PatientProfileSerializer(patient_with_details).data
        # Direct serializer output uses snake_case (camelCase is applied by DRF response middleware)
        for key in ("id", "jhe_user_id", "identifier", "organizations"):
            assert key in data, f"Expected key '{key}' missing from PatientProfileSerializer output"


class TestFullPatientSerializerUnit:
    """Confirm the *original* PatientSerializer still contains all fields (no regression)."""

    @pytest.mark.django_db
    def test_full_serializer_includes_phi(self, patient_with_details):
        data = PatientSerializer(patient_with_details).data
        # Direct serializer output uses snake_case (camelCase is applied by DRF response middleware)
        for key in ("name_family", "name_given", "birth_date", "telecom_phone", "telecom_email"):
            assert key in data, f"Full PatientSerializer should include '{key}'"


class TestJheUserPatientProfileSerializerUnit:
    """Verify the top-level user serializer strips PHI for patient users."""

    @pytest.mark.django_db
    def test_user_level_fields_exclude_phi(self):
        declared = set(JheUserPatientProfileSerializer.Meta.fields)
        for phi_field in ("email", "first_name", "last_name"):
            assert phi_field not in declared

    @pytest.mark.django_db
    def test_user_level_fields_include_expected(self):
        declared = set(JheUserPatientProfileSerializer.Meta.fields)
        for expected in ("id", "patient", "user_type", "is_superuser"):
            assert expected in declared

    @pytest.mark.django_db
    def test_serialized_output_excludes_phi(self, patient_with_details):
        user = patient_with_details.jhe_user
        user.patient = patient_with_details
        data = JheUserPatientProfileSerializer(user).data
        for key in PATIENT_USER_REDACTED_FIELDS:
            assert key not in data

    @pytest.mark.django_db
    def test_nested_patient_excludes_phi(self, patient_with_details):
        user = patient_with_details.jhe_user
        user.patient = patient_with_details
        data = JheUserPatientProfileSerializer(user).data
        nested = data["patient"]
        for key in PATIENT_NESTED_REDACTED_FIELDS:
            assert key not in nested


class TestJheUserSerializerRegressionUnit:
    """The standard JheUserSerializer must still include all fields."""

    @pytest.mark.django_db
    def test_full_user_serializer_includes_all(self):
        declared = set(JheUserSerializer.Meta.fields)
        for field in ("id", "email", "first_name", "last_name", "patient", "user_type", "is_superuser"):
            assert field in declared


# ═══════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — full HTTP round-trip
# ═══════════════════════════════════════════════════════════════════════════


class TestPatientProfileIntegration:
    """Patient user hits GET /api/v1/users/profile and the response body is PHI-free."""

    @pytest.mark.django_db
    def test_status_200(self, patient_client):
        assert patient_client.get(PROFILE_URL).status_code == 200

    @pytest.mark.django_db
    def test_top_level_phi_absent(self, patient_client):
        data = patient_client.get(PROFILE_URL).json()
        for field in PATIENT_USER_REDACTED_FIELDS:
            assert field not in data, f"Top-level PHI '{field}' present in patient profile"

    @pytest.mark.django_db
    def test_nested_patient_phi_absent(self, patient_client):
        data = patient_client.get(PROFILE_URL).json()
        nested = data["patient"]
        for field in PATIENT_NESTED_REDACTED_FIELDS:
            assert field not in nested, f"Nested PHI '{field}' present in patient.* profile"

    @pytest.mark.django_db
    def test_expected_top_level_fields_present(self, patient_client):
        data = patient_client.get(PROFILE_URL).json()
        for field in PATIENT_USER_EXPECTED_FIELDS:
            assert field in data

    @pytest.mark.django_db
    def test_expected_nested_fields_present(self, patient_client):
        data = patient_client.get(PROFILE_URL).json()
        nested = data["patient"]
        for field in PATIENT_NESTED_EXPECTED_FIELDS:
            assert field in nested

    @pytest.mark.django_db
    def test_organizations_included(self, patient_client):
        data = patient_client.get(PROFILE_URL).json()
        orgs = data["patient"]["organizations"]
        assert isinstance(orgs, list)
        assert len(orgs) >= 1

    @pytest.mark.django_db
    def test_user_type_is_patient(self, patient_client):
        data = patient_client.get(PROFILE_URL).json()
        assert data["userType"] == "patient"


class TestPractitionerProfileIntegration:
    """Practitioner profile must remain unchanged (regression guard)."""

    @pytest.mark.django_db
    def test_status_200(self, practitioner_client):
        assert practitioner_client.get(PROFILE_URL).status_code == 200

    @pytest.mark.django_db
    def test_all_fields_present(self, practitioner_client):
        data = practitioner_client.get(PROFILE_URL).json()
        for field in PRACTITIONER_USER_EXPECTED_FIELDS:
            assert field in data, f"Practitioner profile missing '{field}'"

    @pytest.mark.django_db
    def test_email_returned(self, practitioner_client, user):
        data = practitioner_client.get(PROFILE_URL).json()
        assert data["email"] == user.email

    @pytest.mark.django_db
    def test_name_returned(self, practitioner_client, user):
        data = practitioner_client.get(PROFILE_URL).json()
        assert data["firstName"] == (user.first_name or "")
        assert data["lastName"] == (user.last_name or "")

    @pytest.mark.django_db
    def test_user_type_is_practitioner(self, practitioner_client):
        data = practitioner_client.get(PROFILE_URL).json()
        assert data["userType"] == "practitioner"


class TestSuperuserProfileIntegration:
    """Superusers (who are not patients) should still receive a full profile."""

    @pytest.mark.django_db
    def test_status_200(self, superuser_client):
        assert superuser_client.get(PROFILE_URL).status_code == 200

    @pytest.mark.django_db
    def test_email_present(self, superuser_client, superuser):
        data = superuser_client.get(PROFILE_URL).json()
        assert data["email"] == superuser.email

    @pytest.mark.django_db
    def test_is_superuser_true(self, superuser_client):
        data = superuser_client.get(PROFILE_URL).json()
        assert data["isSuperuser"] is True


# ═══════════════════════════════════════════════════════════════════════════
# ACCURACY / EXPECTED-VALUE TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestPatientProfileAccuracy:
    """Verify exact values and structural correctness of the patient profile."""

    @pytest.mark.django_db
    def test_exact_top_level_keys(self, patient_client):
        """The response must contain *exactly* the allowed keys — no more, no less."""
        data = patient_client.get(PROFILE_URL).json()
        assert set(data.keys()) == PATIENT_USER_EXPECTED_FIELDS

    @pytest.mark.django_db
    def test_exact_nested_patient_keys(self, patient_client):
        data = patient_client.get(PROFILE_URL).json()
        assert set(data["patient"].keys()) == PATIENT_NESTED_EXPECTED_FIELDS

    @pytest.mark.django_db
    def test_id_matches_user(self, patient_client, patient):
        data = patient_client.get(PROFILE_URL).json()
        assert data["id"] == patient.jhe_user.id

    @pytest.mark.django_db
    def test_nested_patient_id_matches(self, patient_client, patient):
        data = patient_client.get(PROFILE_URL).json()
        assert data["patient"]["id"] == patient.id

    @pytest.mark.django_db
    def test_jhe_user_id_matches(self, patient_client, patient):
        data = patient_client.get(PROFILE_URL).json()
        assert data["patient"]["jheUserId"] == patient.jhe_user.id

    @pytest.mark.django_db
    def test_organization_structure(self, patient_client, organization):
        data = patient_client.get(PROFILE_URL).json()
        org = data["patient"]["organizations"][0]
        assert org["id"] == organization.id
        assert org["name"] == organization.name

    @pytest.mark.django_db
    def test_is_superuser_false_for_patient(self, patient_client):
        data = patient_client.get(PROFILE_URL).json()
        assert data["isSuperuser"] is False


class TestPractitionerProfileAccuracy:
    """Verify exact values for practitioner profiles."""

    @pytest.mark.django_db
    def test_exact_top_level_keys(self, practitioner_client):
        data = practitioner_client.get(PROFILE_URL).json()
        assert set(data.keys()) == PRACTITIONER_USER_EXPECTED_FIELDS

    @pytest.mark.django_db
    def test_is_superuser_false_for_practitioner(self, practitioner_client):
        data = practitioner_client.get(PROFILE_URL).json()
        assert data["isSuperuser"] is False


# ═══════════════════════════════════════════════════════════════════════════
# REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestProfileRegressions:
    """Guard against previously-working behaviour breaking."""

    @pytest.mark.django_db
    def test_unauthenticated_returns_401_or_403(self):
        """Profile endpoint must require authentication."""
        client = APIClient()
        response = client.get(PROFILE_URL)
        assert response.status_code in (401, 403)

    @pytest.mark.django_db
    def test_patient_without_org_still_returns_200(self, db):
        """A patient with no organization should still get a valid profile."""
        user = JheUser.objects.create_user(
            email="lonely-patient@example.org",
            password="testpass123",
            identifier="lonely-patient",
            user_type="patient",
        )
        client = APIClient()
        client.force_authenticate(user)
        response = client.get(PROFILE_URL)
        assert response.status_code == 200
        data = response.json()
        assert data["patient"]["organizations"] == []
        # PHI must still be stripped
        for field in PATIENT_USER_REDACTED_FIELDS:
            assert field not in data

    @pytest.mark.django_db
    def test_phi_with_explicit_values_not_leaked(self, organization):
        """Create a patient with known PHI and verify none of it appears in the response."""
        user = JheUser.objects.create_user(
            email="phi-leak-check@example.org",
            password="testpass123",
            first_name="SensitiveFirst",
            last_name="SensitiveLast",
            identifier="phi-leak-patient",
            user_type="patient",
        )
        pat = user.patient_profile
        pat.name_family = "SensitiveLast"
        pat.name_given = "SensitiveFirst"
        pat.birth_date = "1985-12-25"
        pat.telecom_phone = "+1-555-999-8888"
        pat.telecom_email = "sensitive@example.org"
        pat.save()
        pat.organizations.add(organization)

        client = APIClient()
        client.force_authenticate(user)
        response = client.get(PROFILE_URL)
        assert response.status_code == 200

        raw = response.content.decode()
        for sensitive in (
            "SensitiveFirst",
            "SensitiveLast",
            "1985-12-25",
            "+1-555-999-8888",
            "sensitive@example.org",
            "phi-leak-check@example.org",
        ):
            assert sensitive not in raw, f"PHI value '{sensitive}' found in response body"

    @pytest.mark.django_db
    def test_practitioner_profile_not_stripped(self, user):
        """After adding the patient PHI filter, practitioner profiles must still be full."""
        client = APIClient()
        client.force_authenticate(user)
        data = client.get(PROFILE_URL).json()
        assert "email" in data
        assert "firstName" in data
        assert "lastName" in data

    @pytest.mark.django_db
    def test_multiple_sequential_calls_consistent(self, patient_client):
        """Repeated calls should return identical structure."""
        r1 = patient_client.get(PROFILE_URL).json()
        r2 = patient_client.get(PROFILE_URL).json()
        assert r1.keys() == r2.keys()
        assert r1["patient"].keys() == r2["patient"].keys()
