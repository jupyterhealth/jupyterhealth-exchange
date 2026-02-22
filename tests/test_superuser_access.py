"""
Tests for superuser-only access to System Settings and Practitioners endpoints.

Validates that only superusers can perform CRUD operations on:
  - /api/v1/jhe_settings
  - /api/v1/practitioners

Test categories:
  - Unit:        IsSuperUser permission class logic
  - Integration: Full HTTP round-trip with superuser, practitioner, patient, and anonymous users
  - Regression:  Superuser access still works, authenticated non-superusers are denied
  - Accuracy:    Correct status codes (403 vs 401) for each user type
"""

import pytest
from rest_framework.test import APIClient

from core.models import JheSetting, JheUser, Practitioner
from core.permissions import IsSuperUser

SETTINGS_URL = "/api/v1/jhe_settings"
PRACTITIONERS_URL = "/api/v1/practitioners"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def superuser_client(superuser):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(superuser)
    return client


@pytest.fixture
def practitioner_client(user):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(user)
    return client


@pytest.fixture
def patient_client(patient):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(patient.jhe_user)
    return client


@pytest.fixture
def anon_client():
    return APIClient()


@pytest.fixture
def sample_setting(db):
    setting = JheSetting(key="test.setting", setting_id=1, value_type="string")
    setting.set_value("string", "test_value")
    setting.save()
    return setting


# ═══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — IsSuperUser permission class
# ═══════════════════════════════════════════════════════════════════════════


class TestIsSuperUserPermissionUnit:
    """Verify the IsSuperUser permission class logic directly."""

    @pytest.mark.django_db
    def test_superuser_is_allowed(self, superuser):
        from unittest.mock import MagicMock

        perm = IsSuperUser()
        request = MagicMock()
        request.user = superuser
        assert perm.has_permission(request, view=None) is True

    @pytest.mark.django_db
    def test_regular_user_is_denied(self, user):
        from unittest.mock import MagicMock

        perm = IsSuperUser()
        request = MagicMock()
        request.user = user
        assert perm.has_permission(request, view=None) is False

    @pytest.mark.django_db
    def test_patient_is_denied(self, patient):
        from unittest.mock import MagicMock

        perm = IsSuperUser()
        request = MagicMock()
        request.user = patient.jhe_user
        assert perm.has_permission(request, view=None) is False

    def test_anonymous_is_denied(self):
        from unittest.mock import MagicMock
        from django.contrib.auth.models import AnonymousUser

        perm = IsSuperUser()
        request = MagicMock()
        request.user = AnonymousUser()
        assert perm.has_permission(request, view=None) is False


# ═══════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — System Settings endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestSettingsSuperuserAccess:
    """Superuser can perform all CRUD operations on jhe_settings."""

    @pytest.mark.django_db
    def test_list(self, superuser_client):
        r = superuser_client.get(SETTINGS_URL)
        assert r.status_code == 200

    @pytest.mark.django_db
    def test_create(self, superuser_client):
        r = superuser_client.post(
            SETTINGS_URL,
            {
                "key": "new.setting",
                "settingId": 999,
                "valueType": "string",
                "value": "hello",
            },
        )
        assert r.status_code == 201

    @pytest.mark.django_db
    def test_retrieve(self, superuser_client, sample_setting):
        r = superuser_client.get(f"{SETTINGS_URL}/{sample_setting.id}")
        assert r.status_code == 200

    @pytest.mark.django_db
    def test_update(self, superuser_client, sample_setting):
        r = superuser_client.put(
            f"{SETTINGS_URL}/{sample_setting.id}",
            {
                "key": "test.setting",
                "settingId": 1,
                "valueType": "string",
                "value": "updated_value",
            },
        )
        assert r.status_code == 200

    @pytest.mark.django_db
    def test_partial_update(self, superuser_client, sample_setting):
        r = superuser_client.patch(
            f"{SETTINGS_URL}/{sample_setting.id}",
            {
                "value": "patched",
                "valueType": "string",
            },
        )
        assert r.status_code == 200

    @pytest.mark.django_db
    def test_delete(self, superuser_client, sample_setting):
        r = superuser_client.delete(f"{SETTINGS_URL}/{sample_setting.id}")
        assert r.status_code == 204


class TestSettingsPractitionerDenied:
    """Non-superuser practitioner is denied on all jhe_settings operations."""

    @pytest.mark.django_db
    def test_list(self, practitioner_client):
        assert practitioner_client.get(SETTINGS_URL).status_code == 403

    @pytest.mark.django_db
    def test_create(self, practitioner_client):
        r = practitioner_client.post(
            SETTINGS_URL,
            {
                "key": "hack.setting",
                "settingId": 1,
                "valueType": "string",
                "value": "x",
            },
        )
        assert r.status_code == 403

    @pytest.mark.django_db
    def test_retrieve(self, practitioner_client, sample_setting):
        assert practitioner_client.get(f"{SETTINGS_URL}/{sample_setting.id}").status_code == 403

    @pytest.mark.django_db
    def test_update(self, practitioner_client, sample_setting):
        r = practitioner_client.put(
            f"{SETTINGS_URL}/{sample_setting.id}",
            {
                "key": "test.setting",
                "settingId": 1,
                "valueType": "string",
                "value": "x",
            },
        )
        assert r.status_code == 403

    @pytest.mark.django_db
    def test_delete(self, practitioner_client, sample_setting):
        assert practitioner_client.delete(f"{SETTINGS_URL}/{sample_setting.id}").status_code == 403


class TestSettingsPatientDenied:
    """Patient user is denied on all jhe_settings operations."""

    @pytest.mark.django_db
    def test_list(self, patient_client):
        assert patient_client.get(SETTINGS_URL).status_code == 403

    @pytest.mark.django_db
    def test_create(self, patient_client):
        r = patient_client.post(
            SETTINGS_URL,
            {
                "key": "hack.setting",
                "settingId": 1,
                "valueType": "string",
                "value": "x",
            },
        )
        assert r.status_code == 403


class TestSettingsAnonymousDenied:
    """Unauthenticated requests return 401."""

    @pytest.mark.django_db
    def test_list(self, anon_client):
        assert anon_client.get(SETTINGS_URL).status_code == 401

    @pytest.mark.django_db
    def test_create(self, anon_client):
        r = anon_client.post(
            SETTINGS_URL,
            {
                "key": "hack.setting",
                "settingId": 1,
                "valueType": "string",
                "value": "x",
            },
        )
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — Practitioners endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestPractitionersSuperuserAccess:
    """Superuser can list and retrieve practitioners."""

    @pytest.mark.django_db
    def test_list(self, superuser_client):
        r = superuser_client.get(PRACTITIONERS_URL)
        assert r.status_code == 200

    @pytest.mark.django_db
    def test_retrieve(self, superuser_client, user):
        r = superuser_client.get(f"{PRACTITIONERS_URL}/{user.practitioner.id}")
        assert r.status_code == 200


class TestPractitionersPractitionerDenied:
    """Non-superuser practitioner is denied on all practitioners operations."""

    @pytest.mark.django_db
    def test_list(self, practitioner_client):
        assert practitioner_client.get(PRACTITIONERS_URL).status_code == 403

    @pytest.mark.django_db
    def test_retrieve(self, practitioner_client, user):
        assert practitioner_client.get(f"{PRACTITIONERS_URL}/{user.practitioner.id}").status_code == 403

    @pytest.mark.django_db
    def test_create(self, practitioner_client, organization):
        r = practitioner_client.post(
            PRACTITIONERS_URL,
            {
                "organizationId": organization.id,
                "telecomEmail": "new@example.com",
                "nameFamily": "last",
                "nameGiven": "first",
            },
        )
        assert r.status_code == 403

    @pytest.mark.django_db
    def test_delete(self, practitioner_client, user):
        r = practitioner_client.delete(f"{PRACTITIONERS_URL}/{user.practitioner.id}")
        assert r.status_code == 403


class TestPractitionersPatientDenied:
    """Patient user is denied on all practitioners operations."""

    @pytest.mark.django_db
    def test_list(self, patient_client):
        assert patient_client.get(PRACTITIONERS_URL).status_code == 403

    @pytest.mark.django_db
    def test_retrieve(self, patient_client, user):
        assert patient_client.get(f"{PRACTITIONERS_URL}/{user.practitioner.id}").status_code == 403


class TestPractitionersAnonymousDenied:
    """Unauthenticated requests return 401."""

    @pytest.mark.django_db
    def test_list(self, anon_client):
        assert anon_client.get(PRACTITIONERS_URL).status_code == 401

    @pytest.mark.django_db
    def test_retrieve_no_auth(self, anon_client):
        assert anon_client.get(f"{PRACTITIONERS_URL}/1").status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# ACCURACY TESTS — correct status codes
# ═══════════════════════════════════════════════════════════════════════════


class TestStatusCodeAccuracy:
    """Verify the distinction between 401 (not authenticated) and 403 (not authorized)."""

    @pytest.mark.django_db
    def test_anon_settings_is_401(self, anon_client):
        assert anon_client.get(SETTINGS_URL).status_code == 401

    @pytest.mark.django_db
    def test_anon_practitioners_is_401(self, anon_client):
        assert anon_client.get(PRACTITIONERS_URL).status_code == 401

    @pytest.mark.django_db
    def test_practitioner_settings_is_403(self, practitioner_client):
        assert practitioner_client.get(SETTINGS_URL).status_code == 403

    @pytest.mark.django_db
    def test_practitioner_practitioners_is_403(self, practitioner_client):
        assert practitioner_client.get(PRACTITIONERS_URL).status_code == 403

    @pytest.mark.django_db
    def test_patient_settings_is_403(self, patient_client):
        assert patient_client.get(SETTINGS_URL).status_code == 403

    @pytest.mark.django_db
    def test_patient_practitioners_is_403(self, patient_client):
        assert patient_client.get(PRACTITIONERS_URL).status_code == 403

    @pytest.mark.django_db
    def test_superuser_settings_is_200(self, superuser_client):
        assert superuser_client.get(SETTINGS_URL).status_code == 200

    @pytest.mark.django_db
    def test_superuser_practitioners_is_200(self, superuser_client):
        assert superuser_client.get(PRACTITIONERS_URL).status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestSuperuserRegressions:
    """Ensure superuser access was not broken during permission lockdown."""

    @pytest.mark.django_db
    def test_settings_crud_roundtrip(self, superuser_client):
        """Full create-read-update-delete cycle for settings."""
        # Create
        r = superuser_client.post(
            SETTINGS_URL,
            {
                "key": "roundtrip.test",
                "settingId": 42,
                "valueType": "string",
                "value": "v1",
            },
        )
        assert r.status_code == 201
        setting_id = r.json()["id"]

        # Read
        r = superuser_client.get(f"{SETTINGS_URL}/{setting_id}")
        assert r.status_code == 200
        assert r.json()["key"] == "roundtrip.test"

        # Update
        r = superuser_client.patch(
            f"{SETTINGS_URL}/{setting_id}",
            {
                "value": "v2",
                "valueType": "string",
            },
        )
        assert r.status_code == 200

        # Delete
        r = superuser_client.delete(f"{SETTINGS_URL}/{setting_id}")
        assert r.status_code == 204

    @pytest.mark.django_db
    def test_settings_data_not_modified_by_denied_attempt(self, practitioner_client, superuser_client, sample_setting):
        """A denied POST from a practitioner must not create any setting."""
        r = practitioner_client.post(
            SETTINGS_URL,
            {
                "key": "evil.setting",
                "settingId": 666,
                "valueType": "string",
                "value": "bad",
            },
        )
        assert r.status_code == 403
        # Confirm nothing was created
        r2 = superuser_client.get(SETTINGS_URL)
        keys = [s["key"] for s in r2.json()["results"]]
        assert "evil.setting" not in keys

    @pytest.mark.django_db
    def test_multiple_sequential_requests_consistent(self, superuser_client):
        """Repeated requests should behave identically."""
        r1 = superuser_client.get(SETTINGS_URL)
        r2 = superuser_client.get(SETTINGS_URL)
        assert r1.status_code == r2.status_code == 200
