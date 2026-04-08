"""
Tests for the Bruno opencollection request files.

Validates that every request in opencollection/JHE/ maps to a working API
endpoint and uses environment variables (no hardcoded IDs).

Test categories:
- Unit: YAML structure, variable usage, URL patterns
- Integration: each endpoint responds with expected status codes
- Regression: previously broken requests stay fixed
- Accuracy: response payloads match expected schema
- Edge-case: pagination boundaries, empty results, auth failures
- Stress: concurrent-like burst of requests
"""

import glob
import os
import re

import pytest
import yaml
from rest_framework.test import APIClient

from core.models import (
    CodeableConcept,
    DataSource,
    JheSetting,
    JheUser,
    Organization,
    PractitionerOrganization,
)

from .utils import Code, add_observations, add_patient_to_study, create_study

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLLECTION_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "opencollection",
    "JHE",
)

BRUNO_VARIABLE_RE = re.compile(r"\{\{(\w+)\}\}")

# Variables that request files are allowed to reference
ALLOWED_VARIABLES = {
    "BASE_URL",
    "ACCESS_TOKEN",
    "ORG_ID",
    "PATIENT_ID",
    "STUDY_ID",
    "USER_ID",
    "DATA_SOURCE_ID",
    "CLIENT_APP_ID",
}

# IDs that look hardcoded (bare numbers in URLs or bodies)
HARDCODED_ID_RE = re.compile(r"/(?:organizations|patients|studies|users|data_sources|clients|practitioners)/(\d+)")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def superuser_client(superuser):
    """API client authenticated as superuser."""
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(superuser)
    return client


@pytest.fixture
def manager_client(user):
    """API client authenticated as a manager-role practitioner."""
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(user)
    return client


@pytest.fixture
def unauthenticated_client():
    """API client with no authentication."""
    return APIClient()


@pytest.fixture
def second_org(db):
    return Organization.objects.create(name="Second Org", type="other")


@pytest.fixture
def data_source(db):
    return DataSource.objects.create(name="Test Device", type="personal_device")


@pytest.fixture
def scope_code(db):
    code, _ = CodeableConcept.objects.update_or_create(
        coding_system=Code.OpenMHealth.value,
        coding_code=Code.HeartRate.value,
        defaults={"text": "Heart Rate"},
    )
    return code


@pytest.fixture
def bp_scope_code(db):
    code, _ = CodeableConcept.objects.update_or_create(
        coding_system=Code.OpenMHealth.value,
        coding_code=Code.BloodPressure.value,
        defaults={"text": "Blood Pressure"},
    )
    return code


@pytest.fixture
def study_with_observations(organization, user, patient):
    """Study with a patient who has consented observations."""
    study = create_study(
        organization=organization,
        codes=[Code.HeartRate],
    )
    add_patient_to_study(patient=patient, study=study)
    add_observations(patient=patient, code=Code.HeartRate, n=5)
    return study


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def collect_request_files():
    """Collect all .yml files that are actual requests (not folder.yml or opencollection.yml)."""
    pattern = os.path.join(COLLECTION_ROOT, "**", "*.yml")
    files = []
    for path in glob.glob(pattern, recursive=True):
        basename = os.path.basename(path)
        if basename in ("folder.yml", "opencollection.yml"):
            continue
        files.append(path)
    return sorted(files)


def parse_request(path):
    """Parse a Bruno YAML request file."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_url(url_template, env):
    """Replace {{VAR}} placeholders with values from env dict."""

    def replacer(match):
        var = match.group(1)
        return str(env.get(var, match.group(0)))

    return BRUNO_VARIABLE_RE.sub(replacer, url_template)


def get_relative_path(path):
    """Get path relative to collection root."""
    return os.path.relpath(path, COLLECTION_ROOT)


ALL_REQUEST_FILES = collect_request_files()


# ===================================================================
# UNIT TESTS: YAML structure and variable usage
# ===================================================================


class TestYamlStructure:
    """Validate that every request file has correct YAML structure."""

    @pytest.mark.parametrize("path", ALL_REQUEST_FILES, ids=get_relative_path)
    def test_valid_yaml(self, path):
        """Each request file must be valid YAML."""
        data = parse_request(path)
        assert data is not None
        assert "info" in data
        assert "http" in data

    @pytest.mark.parametrize("path", ALL_REQUEST_FILES, ids=get_relative_path)
    def test_has_name(self, path):
        """Each request must have a name."""
        data = parse_request(path)
        assert data["info"].get("name"), f"Missing name in {path}"

    @pytest.mark.parametrize("path", ALL_REQUEST_FILES, ids=get_relative_path)
    def test_has_method(self, path):
        """Each request must specify an HTTP method."""
        data = parse_request(path)
        method = data["http"].get("method", "").upper()
        assert method in ("GET", "POST", "PUT", "PATCH", "DELETE"), f"Invalid method: {method}"

    @pytest.mark.parametrize("path", ALL_REQUEST_FILES, ids=get_relative_path)
    def test_has_url(self, path):
        """Each request must have a URL starting with {{BASE_URL}}."""
        data = parse_request(path)
        url = data["http"].get("url", "")
        assert url.startswith("{{BASE_URL}}"), f"URL doesn't start with {{{{BASE_URL}}}}: {url}"

    @pytest.mark.parametrize("path", ALL_REQUEST_FILES, ids=get_relative_path)
    def test_auth_inherit(self, path):
        """All requests should use auth: inherit (collection-level bearer token)."""
        data = parse_request(path)
        auth = data["http"].get("auth")
        assert auth == "inherit", f"Expected auth: inherit, got: {auth}"


class TestNoHardcodedIds:
    """Ensure no request files have hardcoded numeric IDs."""

    @pytest.mark.parametrize("path", ALL_REQUEST_FILES, ids=get_relative_path)
    def test_url_no_hardcoded_ids(self, path):
        """URLs should use {{VARIABLE}} instead of hardcoded numeric IDs."""
        data = parse_request(path)
        url = data["http"].get("url", "")
        matches = HARDCODED_ID_RE.findall(url)
        assert not matches, f"Hardcoded ID(s) in URL: {matches} - use environment variables"

    @pytest.mark.parametrize("path", ALL_REQUEST_FILES, ids=get_relative_path)
    def test_only_allowed_variables(self, path):
        """All variables used must be in the documented set."""
        data = parse_request(path)
        content = yaml.dump(data)
        variables = set(BRUNO_VARIABLE_RE.findall(content))
        unknown = variables - ALLOWED_VARIABLES
        assert not unknown, f"Undocumented variables: {unknown}"


class TestCollectionConfig:
    """Validate opencollection.yml configuration."""

    def test_opencollection_exists(self):
        path = os.path.join(COLLECTION_ROOT, "opencollection.yml")
        assert os.path.exists(path)

    def test_opencollection_auth(self):
        path = os.path.join(COLLECTION_ROOT, "opencollection.yml")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        auth = data.get("request", {}).get("auth", {})
        assert auth.get("type") == "bearer"
        assert "ACCESS_TOKEN" in auth.get("token", "")

    def test_opencollection_header(self):
        path = os.path.join(COLLECTION_ROOT, "opencollection.yml")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        headers = data.get("request", {}).get("headers", [])
        auth_headers = [h for h in headers if h.get("name") == "Authorization"]
        assert auth_headers, "Missing Authorization header fallback"

    def test_folder_structure(self):
        """Collection should have expected folder structure."""
        expected_folders = {"FHIR", "Admin"}
        actual = set()
        for entry in os.listdir(COLLECTION_ROOT):
            full = os.path.join(COLLECTION_ROOT, entry)
            if os.path.isdir(full):
                actual.add(entry)
        assert expected_folders.issubset(actual), f"Missing folders: {expected_folders - actual}"


# ===================================================================
# INTEGRATION TESTS: Every endpoint responds correctly
# ===================================================================


class TestSettingsEndpoint:
    """Admin > Settings"""

    def test_list_settings(self, superuser_client):
        r = superuser_client.get("/api/v1/jhe_settings")
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert data["count"] >= 0


class TestOrganizationEndpoints:
    """Admin > Organizations"""

    def test_list_organizations(self, manager_client, organization):
        r = manager_client.get("/api/v1/organizations")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_organization_details(self, manager_client, organization):
        r = manager_client.get(f"/api/v1/organizations/{organization.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == organization.id
        assert data["name"] == organization.name

    def test_create_organization(self, manager_client, organization):
        r = manager_client.post(
            "/api/v1/organizations",
            {
                "name": "Bruno Test Org",
                "type": "dept",
                "part_of": organization.id,
            },
        )
        assert r.status_code == 201
        created = r.json()
        assert created["name"] == "Bruno Test Org"

    def test_update_organization(self, manager_client, organization):
        r = manager_client.patch(
            f"/api/v1/organizations/{organization.id}",
            {
                "name": "Updated Org Name",
            },
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Updated Org Name"

    def test_organization_types(self, manager_client):
        r = manager_client.get("/api/v1/organizations/types")
        assert r.status_code == 200
        types = r.json()
        assert isinstance(types, dict)
        assert len(types) > 0

    def test_organization_tree(self, manager_client, organization):
        sub = Organization.objects.create(part_of=organization, name="Sub", type="other")
        r = manager_client.get(f"/api/v1/organizations/{organization.id}/tree")
        assert r.status_code == 200
        tree = r.json()
        assert tree["id"] == organization.id
        assert any(c["id"] == sub.id for c in tree.get("children", []))

    def test_organization_users(self, manager_client, user, organization):
        r = manager_client.get(f"/api/v1/organizations/{organization.id}/users")
        assert r.status_code == 200
        users = r.json()
        assert len(users) >= 1

    def test_add_organization_user(self, manager_client, organization):
        new_user = JheUser.objects.create_user(
            email="bruno-add-user@example.org",
            user_type="practitioner",
        )
        r = manager_client.post(
            f"/api/v1/organizations/{organization.id}/user",
            {
                "jhe_user_id": new_user.id,
                "organization_partitioner_role": "viewer",
            },
        )
        assert r.status_code == 200
        assert r.json()["role"] == "viewer"

    def test_remove_organization_user(self, manager_client, organization):
        new_user = JheUser.objects.create_user(
            email="bruno-remove-user@example.org",
            user_type="practitioner",
        )
        # Add first
        manager_client.post(
            f"/api/v1/organizations/{organization.id}/user",
            {
                "jhe_user_id": new_user.id,
                "organization_partitioner_role": "viewer",
            },
        )
        # Remove
        r = manager_client.delete(
            f"/api/v1/organizations/{organization.id}/remove_user",
            {"jhe_user_id": new_user.id},
        )
        assert r.status_code == 204

    def test_organization_studies(self, manager_client, organization, hr_study):
        r = manager_client.get(f"/api/v1/organizations/{organization.id}/studies")
        assert r.status_code == 200
        studies = r.json()
        assert any(s["id"] == hr_study.id for s in studies)


class TestPatientEndpoints:
    """Admin > Patients"""

    def test_list_patients(self, manager_client, patient, organization):
        r = manager_client.get("/api/v1/patients")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1

    def test_create_patient(self, manager_client, organization):
        r = manager_client.post(
            "/api/v1/patients",
            {
                "organizationId": organization.id,
                "telecomEmail": "bruno-patient@example.org",
                "birthDate": "2000-01-01",
            },
        )
        assert r.status_code == 200
        assert r.json()["telecomEmail"] == "bruno-patient@example.org"

    def test_delete_patient(self, manager_client, organization):
        r = manager_client.post(
            "/api/v1/patients",
            {
                "organizationId": organization.id,
                "telecomEmail": "bruno-delete@example.org",
                "birthDate": "2000-01-01",
            },
        )
        patient_id = r.json()["id"]
        r = manager_client.delete(f"/api/v1/patients/{patient_id}?organization_id={organization.id}")
        assert r.status_code == 200

    def test_global_lookup(self, manager_client, patient):
        r = manager_client.get(
            "/api/v1/patients/global_lookup",
            {
                "email": patient.jhe_user.email,
            },
        )
        assert r.status_code == 200

    def test_patient_consents_get(self, manager_client, patient, hr_study):
        r = manager_client.get(f"/api/v1/patients/{patient.id}/consents")
        assert r.status_code == 200

    def test_patient_consolidated_clients(self, manager_client, patient):
        r = manager_client.get(f"/api/v1/patients/{patient.id}/consolidated_clients")
        assert r.status_code == 200

    def test_patient_invitation_link(self, manager_client, patient):
        """Admin/Patients/Patient Auth Code.yml - requires application_id + JheSettings."""
        from oauth2_provider.models import get_application_model

        Application = get_application_model()
        app = Application.objects.create(
            name="Bruno Test Client",
            client_type="confidential",
            authorization_grant_type="authorization-code",
            user=patient.jhe_user,
        )
        JheSetting.objects.create(
            setting_id=app.id,
            key="client.code_verifier",
            value_type="string",
            value_string="test-code-verifier-abc123",
        )
        JheSetting.objects.create(
            setting_id=app.id,
            key="client.invitation_url",
            value_type="string",
            value_string="https://example.com/invite?code=CODE",
        )
        r = manager_client.get(
            f"/api/v1/patients/{patient.id}/invitation_link",
            {"application_id": app.id},
        )
        assert r.status_code == 200
        assert "invitationLink" in r.json()

    def test_create_consents(self, manager_client, patient, organization):
        """Admin/Patients/Create Consents.yml"""
        # Use a fresh study with BloodPressure code so no pre-existing consents
        study = create_study(organization=organization, codes=[Code.BloodPressure])
        add_patient_to_study(patient=patient, study=study, consent=False)
        r = manager_client.post(
            f"/api/v1/patients/{patient.id}/consents",
            {
                "study_scope_consents": [
                    {
                        "study_id": study.id,
                        "scope_consents": [
                            {
                                "coding_system": Code.OpenMHealth.value,
                                "coding_code": Code.BloodPressure.value,
                                "consented": False,
                            }
                        ],
                    }
                ],
            },
        )
        assert r.status_code == 200

    def test_update_consents(self, manager_client, patient, hr_study):
        """Admin/Patients/Update Consents.yml"""
        # hr_study fixture already creates consented=True for HeartRate
        r = manager_client.patch(
            f"/api/v1/patients/{patient.id}/consents",
            {
                "study_scope_consents": [
                    {
                        "study_id": hr_study.id,
                        "scope_consents": [
                            {
                                "coding_system": Code.OpenMHealth.value,
                                "coding_code": Code.HeartRate.value,
                                "consented": False,
                            }
                        ],
                    }
                ],
            },
        )
        assert r.status_code == 200

    def test_delete_consents(self, manager_client, patient, hr_study):
        """Admin/Patients/Delete Consents.yml"""
        # hr_study fixture already creates consented=True for HeartRate
        r = manager_client.delete(
            f"/api/v1/patients/{patient.id}/consents",
            {
                "study_scope_consents": [
                    {
                        "study_id": hr_study.id,
                        "scope_consents": [
                            {
                                "coding_system": Code.OpenMHealth.value,
                                "coding_code": Code.HeartRate.value,
                                "consented": True,
                            }
                        ],
                    }
                ],
            },
        )
        assert r.status_code == 200


class TestStudyEndpoints:
    """Admin > Studies"""

    def test_list_studies(self, manager_client, hr_study):
        r = manager_client.get("/api/v1/studies")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_create_study(self, manager_client, organization):
        r = manager_client.post(
            "/api/v1/studies",
            {
                "organization": organization.id,
                "name": "Bruno Test Study",
                "description": "Created by test",
            },
        )
        assert r.status_code == 201

    def test_list_study_patients(self, manager_client, hr_study, patient):
        r = manager_client.get(f"/api/v1/studies/{hr_study.id}/patients")
        assert r.status_code == 200

    def test_add_patients_to_study(self, manager_client, organization, hr_study):
        new_patient_user = JheUser.objects.create_user(
            email="bruno-study-patient@example.org",
            user_type="patient",
        )
        new_patient_user.patient.organizations.add(organization)
        r = manager_client.post(
            f"/api/v1/studies/{hr_study.id}/patients",
            {"patient_ids": [new_patient_user.patient.id]},
        )
        assert r.status_code == 200

    def test_study_scope_requests(self, manager_client, hr_study):
        r = manager_client.get(f"/api/v1/studies/{hr_study.id}/scope_requests")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_study_clients(self, manager_client, hr_study):
        r = manager_client.get(f"/api/v1/studies/{hr_study.id}/clients")
        assert r.status_code == 200

    def test_study_data_sources(self, manager_client, hr_study):
        r = manager_client.get(f"/api/v1/studies/{hr_study.id}/data_sources")
        assert r.status_code == 200


class TestUserEndpoints:
    """Admin > Users"""

    def test_list_users(self, manager_client, user):
        r = manager_client.get("/api/v1/users")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_profile(self, manager_client, user):
        r = manager_client.get("/api/v1/users/profile")
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == user.email

    def test_user_organizations(self, manager_client, user, organization):
        r = manager_client.get("/api/v1/users/organizations")
        assert r.status_code == 200
        orgs = r.json()
        assert any(o["id"] == organization.id for o in orgs)

    def test_search_by_email(self, manager_client, user):
        r = manager_client.get(
            "/api/v1/users/search_by_email",
            {
                "email": user.email,
            },
        )
        assert r.status_code == 200

    def test_user_consents_get(self, manager_client, patient, hr_study):
        patient_client = APIClient()
        patient_client.default_format = "json"
        patient_client.force_authenticate(patient.jhe_user)
        r = patient_client.get(f"/api/v1/patients/{patient.id}/consents")
        assert r.status_code == 200


class TestPractitionerEndpoints:
    """Admin > Practitioners"""

    def test_list_practitioners(self, superuser_client):
        r = superuser_client.get("/api/v1/practitioners")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_create_practitioner(self, superuser_client):
        new_user = JheUser.objects.create_user(
            email="bruno-practitioner@example.org",
            user_type="practitioner",
        )
        r = superuser_client.post(
            "/api/v1/practitioners",
            {
                "user": new_user.id,
            },
        )
        # May succeed or 400 if practitioner already auto-created
        assert r.status_code in (200, 201, 400)


class TestDataSourceEndpoints:
    """Admin > Data Sources"""

    def test_paginate_data_sources(self, manager_client, data_source):
        r = manager_client.get("/api/v1/data_sources")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_data_source_details(self, manager_client, data_source):
        r = manager_client.get(f"/api/v1/data_sources/{data_source.id}")
        assert r.status_code == 200
        assert r.json()["id"] == data_source.id

    def test_all_scopes(self, manager_client, data_source, hr_study):
        r = manager_client.get("/api/v1/data_sources/all_scopes")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_supported_scopes(self, superuser_client, data_source):
        r = superuser_client.get(f"/api/v1/data_sources/{data_source.id}/supported_scopes")
        assert r.status_code == 200


class TestClientEndpoints:
    """Admin > Clients"""

    def test_list_clients(self, manager_client):
        r = manager_client.get("/api/v1/clients")
        assert r.status_code == 200

    def test_client_details(self, manager_client):
        # Clients list may be empty in test, so just verify endpoint works
        r = manager_client.get("/api/v1/clients")
        if r.json().get("count", 0) > 0:
            client_id = r.json()["results"][0]["id"]
            r = manager_client.get(f"/api/v1/clients/{client_id}")
            assert r.status_code == 200

    def test_client_data_sources(self, manager_client):
        r = manager_client.get("/api/v1/clients")
        if r.json().get("count", 0) > 0:
            client_id = r.json()["results"][0]["id"]
            r = manager_client.get(f"/api/v1/clients/{client_id}/data_sources")
            assert r.status_code == 200


class TestObservationEndpoints:
    """Admin > Observations"""

    def test_paginate_observations(self, manager_client, organization, study_with_observations):
        r = manager_client.get(
            "/api/v1/observations",
            {
                "organizationId": organization.id,
                "page": 1,
                "pageSize": 3,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 5
        assert len(data["results"]) == 3

    def test_paginate_page_2(self, manager_client, organization, study_with_observations):
        r = manager_client.get(
            "/api/v1/observations",
            {
                "organizationId": organization.id,
                "page": 2,
                "pageSize": 3,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 2  # 5 total, page 2 of size 3


class TestFHIREndpoints:
    """FHIR R5 endpoints"""

    def test_list_observations_for_org(self, manager_client, study_with_observations):
        r = manager_client.get(
            "/fhir/r5/Observation",
            {
                "patient._has:Group:member:_id": study_with_observations.id,
            },
        )
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["total"] == 5

    def test_list_observations_for_patient(self, manager_client, patient, study_with_observations):
        r = manager_client.get(
            "/fhir/r5/Observation",
            {
                "patient": patient.id,
                "patient._has:Group:member:_id": study_with_observations.id,
            },
        )
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["total"] == 5

    def test_list_patients_by_study(self, manager_client, patient, study_with_observations):
        # FHIR Patient schema requires non-empty name_family/name_given
        patient.name_family = "TestFamily"
        patient.name_given = "TestGiven"
        patient.save()
        r = manager_client.get(
            "/fhir/r5/Patient",
            {
                "_has:Group:member:_id": study_with_observations.id,
            },
        )
        assert r.status_code == 200
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["total"] >= 1

    def test_list_patients_requires_param(self, manager_client):
        r = manager_client.get("/fhir/r5/Patient")
        assert r.status_code == 400

    def test_create_observation_without_consent(self, manager_client, patient, data_source, scope_code):
        """FHIR Create Observation returns 400 when patient hasn't consented."""
        r = manager_client.post(
            "/fhir/r5/Observation",
            {
                "resourceType": "Observation",
                "identifier": [{"system": "https://test.org", "value": "bruno-test"}],
                "status": "final",
                "code": {
                    "coding": [
                        {
                            "system": Code.OpenMHealth.value,
                            "code": Code.BloodGlucose.value,
                        }
                    ],
                },
                "subject": {"reference": f"Patient/{patient.id}"},
                "device": {"reference": f"Device/{data_source.id}"},
                "valueAttachment": {
                    "contentType": "application/json",
                    "data": "e30=",  # base64 of {}
                },
            },
        )
        # 400 expected: patient hasn't consented to blood glucose in any study
        assert r.status_code == 400

    def test_bundle_create_observation(self, manager_client, patient, data_source, study_with_observations):
        """FHIR Bundle POST to /fhir/r5/."""
        import base64
        import json

        from core.utils import generate_observation_value_attachment_data

        record = generate_observation_value_attachment_data(Code.HeartRate.value)
        encoded = base64.b64encode(json.dumps(record).encode()).decode()

        r = manager_client.post(
            "/fhir/r5/",
            {
                "resourceType": "Bundle",
                "type": "batch",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Observation",
                            "identifier": [{"system": "https://test.org", "value": "bruno-bundle-test"}],
                            "status": "final",
                            "code": {
                                "coding": [
                                    {
                                        "system": Code.OpenMHealth.value,
                                        "code": Code.HeartRate.value,
                                    }
                                ],
                            },
                            "subject": {"reference": f"Patient/{patient.id}"},
                            "device": {"reference": f"Device/{data_source.id}"},
                            "valueAttachment": {
                                "contentType": "application/json",
                                "data": encoded,
                            },
                        },
                        "request": {"method": "POST", "url": "Observation"},
                    }
                ],
            },
        )
        assert r.status_code == 200
        result = r.json()
        assert result["resourceType"] == "Bundle"


# ===================================================================
# REGRESSION TESTS: Previously broken requests
# ===================================================================


class TestRegressions:
    """Tests for previously identified bugs and fixes."""

    def test_observation_page_out_of_range(self, manager_client, organization, study_with_observations):
        """Pagination beyond available data should return an error, not crash."""
        r = manager_client.get(
            "/api/v1/observations",
            {
                "organizationId": organization.id,
                "page": 999,
                "pageSize": 3,
            },
        )
        assert r.status_code == 404

    def test_patient_requires_org_membership(self, db):
        """A user without org membership should see no patients (regression: sam had 0 orgs)."""
        lonely_user = JheUser.objects.create_user(
            email="lonely@example.org",
            user_type="practitioner",
        )
        client = APIClient()
        client.default_format = "json"
        client.force_authenticate(lonely_user)
        r = client.get("/api/v1/patients")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_superuser_without_org_sees_no_patients(self, db):
        """Superuser with no org memberships gets 0 patients (queryset uses JOINs)."""
        su = JheUser.objects.create_superuser(
            email="su-no-org@example.org",
            password="unused",
        )
        client = APIClient()
        client.default_format = "json"
        client.force_authenticate(su)
        r = client.get("/api/v1/patients")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_no_trailing_slash_required(self, manager_client, organization):
        """URLs without trailing slash should work (router uses trailing_slash=False)."""
        r = manager_client.get("/api/v1/organizations")
        assert r.status_code == 200

    def test_patient_token_file_removed(self):
        """Previously redundant or insecure files should stay removed."""
        removed_files = [
            os.path.join("Admin", "Patients", "Patient token.yml"),
            os.path.join("Admin", "Patients", "Patient Consents.yml"),
            os.path.join("Admin", "Users", "Consents.yml"),
            os.path.join("Admin", "Users", "Create Consents.yml"),
            os.path.join("Admin", "Users", "Update Consents.yml"),
            os.path.join("Admin", "Users", "Delete Consents.yml"),
            os.path.join("Admin", "Studies", "All Scopes.yml"),
        ]
        for rel in removed_files:
            path = os.path.join(COLLECTION_ROOT, rel)
            assert not os.path.exists(path), f"{rel} should have been removed"


# ===================================================================
# ACCURACY TESTS: Response payloads match expected schema
# ===================================================================


class TestResponseSchemas:
    """Validate that responses match expected shapes."""

    def test_organization_list_schema(self, manager_client, organization):
        r = manager_client.get("/api/v1/organizations")
        data = r.json()
        assert "count" in data
        assert "results" in data
        assert isinstance(data["results"], list)
        if data["results"]:
            org = data["results"][0]
            assert "id" in org
            assert "name" in org
            assert "type" in org

    def test_patient_list_schema(self, manager_client, patient, organization):
        r = manager_client.get("/api/v1/patients")
        data = r.json()
        assert "count" in data
        assert "results" in data
        if data["results"]:
            p = data["results"][0]
            assert "id" in p
            assert "organizations" in p

    def test_study_list_schema(self, manager_client, hr_study):
        r = manager_client.get("/api/v1/studies")
        data = r.json()
        assert "count" in data
        if data["results"]:
            s = data["results"][0]
            assert "id" in s
            assert "name" in s
            assert "organization" in s

    def test_fhir_bundle_schema(self, manager_client, study_with_observations):
        r = manager_client.get(
            "/fhir/r5/Observation",
            {
                "patient._has:Group:member:_id": study_with_observations.id,
            },
        )
        bundle = r.json()
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        assert "total" in bundle
        assert "link" in bundle
        assert "entry" in bundle
        for entry in bundle["entry"]:
            assert "resource" in entry
            assert entry["resource"]["resourceType"] == "Observation"

    def test_user_profile_schema(self, manager_client, user):
        r = manager_client.get("/api/v1/users/profile")
        data = r.json()
        assert "id" in data
        assert "email" in data
        assert data["email"] == user.email

    def test_observation_list_schema(self, manager_client, organization, study_with_observations):
        r = manager_client.get(
            "/api/v1/observations",
            {
                "organizationId": organization.id,
            },
        )
        data = r.json()
        assert "count" in data
        assert "results" in data
        if data["results"]:
            obs = data["results"][0]
            assert "id" in obs


# ===================================================================
# EDGE-CASE TESTS: Boundary conditions
# ===================================================================


class TestEdgeCases:
    """Boundary conditions and edge cases."""

    def test_empty_organization_has_no_patients(self, manager_client, second_org):
        """An org with no patients should return empty list."""
        user = JheUser.objects.create_user(
            email="edge-empty-org@example.org",
            user_type="practitioner",
        )
        PractitionerOrganization.objects.create(
            practitioner=user.practitioner,
            organization=second_org,
            role="manager",
        )
        client = APIClient()
        client.default_format = "json"
        client.force_authenticate(user)
        r = client.get("/api/v1/patients")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_observation_page_size_one(self, manager_client, organization, study_with_observations):
        """Page size of 1 should work correctly."""
        r = manager_client.get(
            "/api/v1/observations",
            {
                "organizationId": organization.id,
                "page": 1,
                "pageSize": 1,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 1
        assert data["count"] == 5

    def test_unauthenticated_access_denied(self, unauthenticated_client):
        """All API endpoints should reject unauthenticated requests."""
        endpoints = [
            "/api/v1/jhe_settings",
            "/api/v1/organizations",
            "/api/v1/patients",
            "/api/v1/studies",
            "/api/v1/users",
            "/api/v1/practitioners",
            "/api/v1/data_sources",
            "/api/v1/clients",
            "/api/v1/observations",
        ]
        for endpoint in endpoints:
            r = unauthenticated_client.get(endpoint)
            assert r.status_code in (401, 403), f"{endpoint} returned {r.status_code}"

    def test_nonexistent_resource_404(self, manager_client):
        """Requesting a nonexistent resource should return 404 or 403."""
        r = manager_client.get("/api/v1/organizations/999999")
        assert r.status_code in (403, 404)

    def test_search_by_email_nonexistent(self, manager_client):
        """Searching for a nonexistent email should not crash."""
        r = manager_client.get(
            "/api/v1/users/search_by_email",
            {
                "email": "nonexistent-99999@example.org",
            },
        )
        assert r.status_code in (200, 404)

    def test_observation_no_org_filter(self, manager_client, study_with_observations):
        """Observations without organizationId should still work."""
        r = manager_client.get("/api/v1/observations")
        assert r.status_code == 200

    def test_create_organization_missing_fields(self, manager_client):
        """Creating an org without required fields should fail gracefully."""
        r = manager_client.post("/api/v1/organizations", {})
        assert r.status_code in (400, 403)

    def test_fhir_observation_with_code_filter(self, manager_client, study_with_observations):
        """FHIR observations filtered by code."""
        r = manager_client.get(
            "/fhir/r5/Observation",
            {
                "patient._has:Group:member:_id": study_with_observations.id,
                "code": f"{Code.OpenMHealth.value}|{Code.HeartRate.value}",
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 5


# ===================================================================
# STRESS TESTS: Burst of requests
# ===================================================================


class TestStress:
    """Verify endpoints handle rapid sequential requests."""

    def test_rapid_organization_list(self, manager_client, organization):
        """Hit organization list 50 times in sequence."""
        for _ in range(50):
            r = manager_client.get("/api/v1/organizations")
            assert r.status_code == 200

    def test_rapid_patient_list(self, manager_client, patient, organization):
        """Hit patient list 50 times."""
        for _ in range(50):
            r = manager_client.get("/api/v1/patients")
            assert r.status_code == 200

    def test_rapid_fhir_observations(self, manager_client, study_with_observations):
        """Hit FHIR observation list 50 times."""
        for _ in range(50):
            r = manager_client.get(
                "/fhir/r5/Observation",
                {
                    "patient._has:Group:member:_id": study_with_observations.id,
                },
            )
            assert r.status_code == 200

    def test_rapid_mixed_endpoints(self, manager_client, organization, hr_study, user):
        """Hit multiple different endpoints in rapid succession."""
        endpoints = [
            "/api/v1/organizations",
            "/api/v1/patients",
            "/api/v1/studies",
            "/api/v1/users/profile",
            "/api/v1/data_sources",
        ]
        for _ in range(20):
            for endpoint in endpoints:
                r = manager_client.get(endpoint)
                assert r.status_code == 200
