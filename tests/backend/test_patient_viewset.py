import pytest
from django.db import connection
from django.db.utils import IntegrityError
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import (
    Organization,
    Patient,
    PatientIdentifier,
    StudyPatientScopeConsent,
)

from .utils import (
    Code,
    add_patient_to_study,
    add_patients,
    assert_valid_fhir_bundle,
    create_study,
    fetch_paginated,
)


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


def test_fhir_list_patients(api_client, organization, hr_study):
    # A practitioner may now list every patient in their organizations without a study filter
    # (organization membership is the access boundary).
    n = 25
    per_page = 10
    existing = Patient.objects.all().count()
    add_patients(n - existing, organization)
    patients = fetch_paginated(api_client, "/FHIR/R5/Patient", {"_count": per_page})
    assert len(patients) == n


def test_fhir_list_patients_by_study(api_client, organization, hr_study):
    n = 25
    per_page = 10
    existing = Patient.objects.all().count()
    add_patients(n - existing, organization)
    for patient in Patient.objects.all():
        add_patient_to_study(patient, hr_study)
    patients = fetch_paginated(
        api_client,
        "/FHIR/R5/Patient",
        {"_count": per_page, "patient._has:Group:member:_id": hr_study.id},
    )
    assert len(patients) == n


def test_patient_pagination(api_client, organization):
    n = 25
    per_page = 10
    study = create_study(organization=organization, codes=[Code.HeartRate])
    for patient in add_patients(n, organization=organization):
        add_patient_to_study(patient, study)

    params = {"patient._has:Group:member:_id": study.id, "_count": per_page}
    with CaptureQueriesContext(connection) as ctx:
        r = api_client.get("/FHIR/R5/Patient", params)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["resourceType"] == "Bundle"
    assert page["type"] == "searchset"
    assert page["total"] == n
    assert len(page["entry"]) == per_page
    # paginated at the DB level (LIMIT, no OFFSET on page 1); identifiers are prefetched in a
    # separate bounded query, so locate the paginated query rather than assuming it is last.
    paginated_queries = [q["sql"] for q in ctx.captured_queries if "LIMIT 10" in q["sql"]]
    assert len(paginated_queries) == 1
    assert "OFFSET" not in paginated_queries[0]
    # the search Bundle envelope (and its nested Patient resources) is valid FHIR
    assert_valid_fhir_bundle(page)

    pages = fetch_paginated(api_client, "/FHIR/R5/Patient", params, return_pages=True)
    assert len(pages) == 3
    assert len(pages[0]["entry"]) == per_page
    assert len(pages[-1]["entry"]) == n % per_page
    assert sum(len(p["entry"]) for p in pages) == n
    for bundle in pages:
        assert_valid_fhir_bundle(bundle)
    # no 'next' link on the last page
    link_rels = [link["relation"] for link in pages[-1]["link"]]
    assert "next" not in link_rels


def test_fhir_list_patients_by_identifier(api_client, organization):
    # Two patients with distinct external identifiers; the FHIR search must return only the match.
    patient_a, patient_b = add_patients(2, organization=organization)
    PatientIdentifier.objects.create(patient=patient_a, system="http://hospital-a.org", value="MRN-A")
    PatientIdentifier.objects.create(patient=patient_b, system="http://hospital-b.org", value="MRN-B")

    # identifier param is "system|value"; only the value is used to filter (system is optional).
    bundle = fetch_paginated(api_client, "/FHIR/R5/Patient", {"identifier": "http://hospital-a.org|MRN-A"})
    assert len(bundle) == 1
    assert bundle[0]["resource"]["id"] == str(patient_a.id)

    # only the value (after "|") filters, so a system-less value is written as "|value"
    bundle = fetch_paginated(api_client, "/FHIR/R5/Patient", {"identifier": "|MRN-B"})
    assert len(bundle) == 1
    assert bundle[0]["resource"]["id"] == str(patient_b.id)

    # a non-matching identifier yields an empty bundle, not an error
    r = api_client.get("/FHIR/R5/Patient", {"identifier": "|MRN-NONE"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


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
    PatientIdentifier.objects.create(patient=patient_a, system="http://hospital-a.org", value="MRN-DUP")
    with pytest.raises(IntegrityError):
        PatientIdentifier.objects.create(patient=patient_b, system="http://hospital-a.org", value="MRN-DUP")


def test_global_add_organization_duplicate(api_client, organization):
    # Adding a patient who is already a member of the organization must return a
    # clear 400 (issue #238), not a 500 internal server error.
    r = api_client.post(
        "/api/v1/patients",
        {
            "organizationId": organization.id,
            "telecomEmail": "dup-org-member@example.com",
            "birthDate": "2000-01-01",
        },
        format="json",
    )
    assert r.status_code == 200, r.text
    patient_id = r.json()["id"]

    r = api_client.patch(f"/api/v1/patients/{patient_id}/global_add_organization?organization_id={organization.id}")
    assert r.status_code == 400, r.text
    assert "already a member" in r.text


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
