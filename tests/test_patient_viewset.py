import pytest
from django.db.utils import IntegrityError
from rest_framework.test import APIClient

from core.models import (
    Organization,
    Patient,
    PatientIdentifier,
    StudyPatientScopeConsent,
)

from .utils import Code, add_patient_to_study, add_patients, fetch_paginated


def test_patient_practitioner_can_update_own_consents(hr_study):
    patient_1, patient_2 = add_patients(2, organization=hr_study.organization)
    for patient in (patient_1, patient_2):
        add_patient_to_study(patient, hr_study, consent=False)
    client = APIClient()
    client.force_authenticate(patient_1.jhe_user)
    payload = {
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
        ]
    }
    response = client.post(
        f"/api/v1/patients/{patient_1.id}/consents",
        data=payload,
        format="json",
    )
    assert response.status_code == 200
    created = StudyPatientScopeConsent.objects.filter(
        study_patient__patient=patient_1,
        scope_code__coding_code=Code.HeartRate.value,
    )
    assert created.count() == 1
    # now test the _other_ patient
    response = client.post(
        f"/api/v1/patients/{patient_2.id}/consents",
        data=payload,
        format="json",
    )
    assert response.status_code == 403
    created = StudyPatientScopeConsent.objects.filter(
        study_patient__patient=patient_2,
        scope_code__coding_code=Code.HeartRate.value,
    )
    assert created.count() == 0


def test_list_patients(api_client, organization):
    n = 25
    per_page = 10
    existing = Patient.objects.all().count()
    add_patients(n - existing, organization)
    patients = fetch_paginated(api_client, "/api/v1/patients", {"pageSize": per_page})
    assert len(patients) == n
    new_org = Organization.objects.create(name="Other", type="other")
    add_patients(10, new_org)
    patients = fetch_paginated(api_client, "/api/v1/patients", {"pageSize": per_page})
    assert len(patients) == n


def test_create_delete(api_client, organization):
    email = "testcreate-patient@example.com"
    r = api_client.post(
        "/api/v1/patients",
        {
            "organizationId": organization.id,
            "telecomEmail": email,
            "birthDate": "2000-01-01",
        },
        format="json",
    )
    assert r.status_code == 200, r.text
    patient_info = r.json()
    assert "id" in patient_info
    assert patient_info["telecomEmail"] == email
    assert patient_info["organizations"]
    assert patient_info["organizations"][0]["id"] == organization.id
    r = api_client.get(f"/api/v1/patients/{patient_info['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == patient_info

    r = api_client.delete(f"/api/v1/patients/{patient_info['id']}?organization_id={organization.id}")
    assert r.status_code == 200, r.text
    assert r.json()["success"]


@pytest.mark.xfail(reason="invalid inputs to create should be handled")
def test_create_validation(api_client, organization):
    # test validation of create inputs
    r = api_client.post(
        "/api/v1/patients",
        {
            "organizationId": organization.id,
            "telecom_email": "testcreate-patient@example.com",
        },
        format="json",
    )
    assert r.status_code == 400


@pytest.mark.xfail(reason="not permitted to list patients in fhir without study")
def test_fhir_list_patients(api_client, organization, hr_study):
    n = 25
    per_page = 10
    existing = Patient.objects.all().count()
    add_patients(n - existing, organization)
    patients = fetch_paginated(api_client, "/fhir/r5/Patient", {"_count": per_page})
    assert len(patients) == n


@pytest.mark.xfail(reason="fhir list patients query is wrong")
def test_fhir_list_patients_by_study(api_client, organization, hr_study):
    n = 25
    per_page = 10
    existing = Patient.objects.all().count()
    add_patients(n - existing, organization)
    for patient in Patient.objects.all():
        add_patient_to_study(patient, hr_study)
    patients = fetch_paginated(
        api_client,
        "/fhir/r5/Patient",
        {"_count": per_page, "_has:Group:member:_id": hr_study.id},
    )
    assert len(patients) == n


def test_fhir_list_patients_by_identifier(api_client, organization):
    pytest.skip("not implemented")


def test_create_with_identifiers(api_client, organization):
    r = api_client.post(
        "/api/v1/patients",
        {
            "organizationId": organization.id,
            "telecomEmail": "multi-id@example.com",
            "birthDate": "2000-01-01",
            "identifiers": [
                {"system": "http://hospital-a.org", "value": "MRN-001"},
                {"system": "http://hospital-b.org", "value": "MRN-002"},
            ],
        },
        format="json",
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["identifiers"]) == 2
    systems = {i["system"] for i in data["identifiers"]}
    assert systems == {"http://hospital-a.org", "http://hospital-b.org"}


def test_identifier_unique_system_value_constraint(organization):
    patient_a, patient_b = add_patients(2, organization=organization)
    PatientIdentifier.objects.create(
        patient=patient_a, system="http://hospital-a.org", value="MRN-DUP"
    )
    with pytest.raises(IntegrityError):
        PatientIdentifier.objects.create(
            patient=patient_b, system="http://hospital-a.org", value="MRN-DUP"
        )


def test_update_replaces_identifiers(api_client, organization):
    r = api_client.post(
        "/api/v1/patients",
        {
            "organizationId": organization.id,
            "telecomEmail": "replace-ids@example.com",
            "birthDate": "2000-01-01",
            "identifiers": [
                {"system": "http://hospital-a.org", "value": "OLD-001"},
            ],
        },
        format="json",
    )
    assert r.status_code == 200, r.text
    patient_id = r.json()["id"]

    r = api_client.patch(
        f"/api/v1/patients/{patient_id}?organizationId={organization.id}",
        {
            "identifiers": [
                {"system": "http://hospital-b.org", "value": "NEW-001"},
                {"system": "http://hospital-c.org", "value": "NEW-002"},
            ],
        },
        format="json",
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["identifiers"]) == 2
    systems = {i["system"] for i in data["identifiers"]}
    assert systems == {"http://hospital-b.org", "http://hospital-c.org"}
