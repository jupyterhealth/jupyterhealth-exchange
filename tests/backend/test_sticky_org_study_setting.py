"""Issue #82 (sticky org/study select): selecting an organization/study persists it to
the practitioner's settings, and the profile endpoint returns those settings so the UI can
restore the selection on the next visit. This guards the previously-untested backend half
of the feature (the restore itself is client-side)."""

from rest_framework.test import APIClient

from .utils import Code, create_study


def _practitioner_api(user):
    api = APIClient()
    api.default_format = "json"
    api.force_authenticate(user)
    return api


def test_patient_list_persists_current_org_and_study(user, organization):
    study = create_study(organization=organization, codes=[Code.HeartRate])
    api = _practitioner_api(user)

    r = api.get("/api/v1/patients", {"organization_id": organization.id, "study_id": study.id})

    assert r.status_code == 200, r.text
    user.practitioner.refresh_from_db()
    assert user.practitioner.get_setting("current_organization_id") == organization.id
    assert user.practitioner.get_setting("current_study_id") == study.id


def test_patient_list_without_study_clears_current_study(user, organization):
    study = create_study(organization=organization, codes=[Code.HeartRate])
    api = _practitioner_api(user)

    api.get("/api/v1/patients", {"organization_id": organization.id, "study_id": study.id})
    api.get("/api/v1/patients", {"organization_id": organization.id})  # no study -> "All Studies"

    user.practitioner.refresh_from_db()
    assert user.practitioner.get_setting("current_study_id") is None
    # org is still remembered
    assert user.practitioner.get_setting("current_organization_id") == organization.id


def test_study_list_does_not_clear_current_study(user, organization):
    # /api/v1/studies also populates the Study dropdown on the Patients/Observations/FHIR pages
    # (called WITHOUT study_id), so it must NOT clear the saved study -- clearing here broke sticky
    # study restore (issue #82). The clear belongs to the patient/observation list views, which run
    # when the user explicitly picks "All Studies".
    study = create_study(organization=organization, codes=[Code.HeartRate])
    api = _practitioner_api(user)

    # The real save path: a patient list with a study_id persists current_study_id.
    api.get("/api/v1/patients", {"organization_id": organization.id, "study_id": study.id})
    # A studies-list dropdown sub-fetch (no study_id) must leave the saved study intact.
    api.get("/api/v1/studies", {"organization_id": organization.id})

    user.practitioner.refresh_from_db()
    assert user.practitioner.get_setting("current_study_id") == study.id


def test_profile_exposes_practitioner_settings_for_restore(user, organization):
    api = _practitioner_api(user)
    api.get("/api/v1/patients", {"organization_id": organization.id})

    r = api.get("/api/v1/users/profile")

    assert r.status_code == 200, r.text
    # Keys are camelCased on the way out; the UI reads settings.currentOrganizationId.
    assert r.json()["settings"]["currentOrganizationId"] == organization.id
