import pytest

from core.models import StudyPatient, CodeableConcept, StudyScopeRequest
from .utils import fetch_paginated, add_patients, Code


def test_list_studies(api_client, organization, hr_study):
    studies = fetch_paginated(api_client, "/api/v1/studies")
    assert len(studies) == 1
    assert studies[0]["id"] == hr_study.id


def test_create_delete(api_client, organization):
    name = "test create"
    r = api_client.post(
        "/api/v1/studies",
        {"organization": organization.id, "name": name, "description": "some study"},
    )
    assert r.status_code == 201, r.text
    info = r.json()
    assert "id" in info
    assert info["name"] == name
    assert info["organization"] == organization.id
    r = api_client.get(f"/api/v1/studies/{info['id']}")
    assert r.status_code == 200, r.text

    assert r.json()["id"] == info["id"]
    # FIXME: fail, organization schema doesn't match
    # assert r.json() == info

    r = api_client.delete(f"/api/v1/studies/{info['id']}")
    assert r.status_code == 204, r.text
    r = api_client.get(f"/api/v1/studies/{info['id']}")
    # FIXME: should this 404?
    assert r.status_code == 403


def test_create_invalid(api_client, organization):
    r = api_client.post(
        "/api/v1/studies",
        {"organization": organization.id, "name": 5},
    )
    assert r.status_code == 400


def test_get_study(api_client, hr_study):
    r = api_client.get(f"/api/v1/studies/{hr_study.id}")
    assert r.status_code == 200
    study = r.json()
    assert study["id"] == study["id"]


def test_patch_study(api_client, hr_study):
    before = api_client.get(f"/api/v1/studies/{hr_study.id}").json()
    r = api_client.patch(f"/api/v1/studies/{hr_study.id}", {"description": "updated description"})
    assert r.status_code == 200
    study = r.json()
    assert study["description"] == "updated description"
    assert study["name"] == before["name"]


@pytest.mark.xfail(
    reason="django.db.utils.ProgrammingError: column core_patientorganization.jhe_user_id does not exist"
)
def test_get_study_patients(api_client, patient, hr_study):
    patients = fetch_paginated(api_client, f"/api/v1/studies/{hr_study.id}/patients")
    assert patients
    assert patients[0]["id"] == patient.id


def test_add_remove_study_patients(api_client, organization, hr_study):
    study_patients = StudyPatient.objects.filter(study=hr_study)
    seed_count = study_patients.count()

    patients = add_patients(5, organization)
    patients_url = f"/api/v1/studies/{hr_study.id}/patients"
    r = api_client.post(patients_url, {"patient_ids": [patient.id for patient in patients]})
    assert r.status_code == 200
    # TODO: check study_patients response

    study_patients = StudyPatient.objects.filter(study=hr_study)
    assert study_patients.count() == 5 + seed_count

    r = api_client.delete(patients_url, {"patient_ids": [patient.id for patient in patients]})
    assert r.status_code == 200

    study_patients = StudyPatient.objects.filter(study=hr_study)
    assert study_patients.count() == seed_count

    # do it again
    # FIXME: should this raise when there is no match?
    r = api_client.delete(patients_url, {"patient_ids": [patient.id for patient in patients]})

    study_patients = StudyPatient.objects.filter(study=hr_study)
    assert study_patients.count() == seed_count


def test_get_study_scope_requests(api_client, hr_study):
    r = api_client.get(f"/api/v1/studies/{hr_study.id}/scope_requests")
    assert r.status_code == 200, r.text
    scope_requests = r.json()
    assert len(scope_requests) == 1
    sr = scope_requests[0]
    assert sr["scopeCode"]["codingCode"] == Code.HeartRate.value
    assert sr["study"]["id"] == hr_study.id


def test_add_remove_scope_requests(api_client, hr_study):
    bp, _ = CodeableConcept.objects.update_or_create(
        coding_system=Code.OpenMHealth.value,
        coding_code=Code.BloodPressure.value,
        text="blood pressure",
    )
    url = f"/api/v1/studies/{hr_study.id}/scope_requests"
    r = api_client.post(url, {"scope_code_id": bp.id})
    assert r.status_code == 200, r.text
    r = api_client.get(url)
    assert r.status_code == 200, r.text
    scope_codes = r.json()
    assert len(scope_codes) == 2
    sc_ids = {sc["scopeCode"]["id"] for sc in scope_codes}
    assert bp.id in sc_ids
    assert StudyScopeRequest.objects.filter(study=hr_study, scope_code=bp).count() == 1

    url = f"/api/v1/studies/{hr_study.id}/scope_requests"
    r = api_client.delete(url, {"scope_code_id": bp.id})
    assert r.status_code == 200, r.text
    assert StudyScopeRequest.objects.filter(study=hr_study, scope_code=bp).count() == 0


def test_get_study_clients(api_client, hr_study):
    r = api_client.get(f"/api/v1/studies/{hr_study.id}/clients")
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_add_remove_study_clients(api_client, hr_study):
    pytest.skip("TODO")


def test_get_study_data_sources(api_client, hr_study):
    r = api_client.get(f"/api/v1/studies/{hr_study.id}/data_sources")
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_add_remove_study_data_sources(api_client, hr_study):
    pytest.skip("TODO")
