"""A practitioner registers a FhirSource for a patient and uploads FHIR resources under it.

Every practitioner write needs organization access to the patient plus the "patient.manage_data"
permission (core/permissions.py), both when registering the source (``patient_id`` on create) and
when uploading under it (the X-JHE-FHIR-Source-ID write path).
"""

import pytest
from rest_framework.test import APIClient

from core.models import (
    EhrBrand,
    EhrBrandLocation,
    EhrVendor,
    FhirAuxResource,
    FhirSource,
    JheUser,
    Organization,
    PractitionerOrganization,
)


def _client(user):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(user)
    return client


def _practitioner(email, organization, role):
    user = JheUser.objects.create_user(email=email, password="unused", user_type="practitioner")
    PractitionerOrganization.objects.create(practitioner=user.practitioner, organization=organization, role=role)
    return user


@pytest.fixture
def viewer(organization):
    # Shares the patient's organization, but the viewer role has no patient.manage_data.
    return _practitioner("viewer@example.org", organization, "viewer")


@pytest.fixture
def stranger(device):
    # A patient in an organization the practitioner is not a member of, with a source of their own.
    other = Organization.objects.create(name="Other", type="other")
    patient = JheUser.objects.create_user(email="stranger@example.org", user_type="patient").patient
    patient.organizations.add(other)
    FhirSource.objects.create(patient=patient, data_source=device, label="Stranger source")
    return patient


def _r4_condition(patient, resource_id="epic-cond-1"):
    return {
        "resourceType": "Condition",
        "id": resource_id,
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "code": {"text": "HTN"},
        "subject": {"reference": f"Patient/{patient.id}"},
    }


# -- registering a source --


def test_practitioner_registers_a_source_for_a_patient(api_client, patient, device):
    r = api_client.post("/api/v1/fhir_sources", {"patient_id": patient.id, "data_source": device.id, "label": "Upload"})
    assert r.status_code == 201, r.content
    assert r.json()["patient"] == patient.id
    assert "patientId" not in r.json()  # write-only (responses are camelCased)
    assert FhirSource.objects.get(pk=r.json()["id"]).patient_id == patient.id


def test_practitioner_must_name_the_patient(api_client, device):
    r = api_client.post("/api/v1/fhir_sources", {"data_source": device.id})
    assert r.status_code == 400, r.content
    assert "patientId" in r.json()


def test_patient_user_cannot_name_a_patient(patient, device):
    # A patient's sources are always their own; naming any patient, even themself, is refused.
    r = _client(patient.jhe_user).post("/api/v1/fhir_sources", {"patient_id": patient.id, "data_source": device.id})
    assert r.status_code == 400, r.content
    assert not FhirSource.objects.filter(patient=patient).exists()


def test_viewer_cannot_register_a_source(viewer, patient, device):
    r = _client(viewer).post("/api/v1/fhir_sources", {"patient_id": patient.id, "data_source": device.id})
    assert r.status_code == 403, r.content
    assert not FhirSource.objects.filter(patient=patient).exists()


def test_practitioner_cannot_register_a_source_for_a_patient_outside_their_organizations(api_client, stranger, device):
    r = api_client.post("/api/v1/fhir_sources", {"patient_id": stranger.id, "data_source": device.id})
    assert r.status_code == 403, r.content
    assert FhirSource.objects.filter(patient=stranger).count() == 1


def test_unknown_patient_is_refused_like_an_inaccessible_one(api_client, device):
    # Same 403 as a patient outside the practitioner's organizations, so existence is not revealed.
    r = api_client.post("/api/v1/fhir_sources", {"patient_id": 999999, "data_source": device.id})
    assert r.status_code == 403, r.content


def test_practitioner_gets_the_patients_existing_source_for_a_brand(api_client, patient, device):
    # The one-source-per-brand rule holds whoever registers it.
    vendor = EhrVendor.objects.create(name="Epic")
    brand = EhrBrand.objects.create(name="Mercy", vendor=vendor, fhir_base_url="https://mercy.example.org/FHIR/R4")
    location = EhrBrandLocation.objects.create(brand=brand, name="Mercy St. Louis")
    first = _client(patient.jhe_user).post(
        "/api/v1/fhir_sources", {"data_source": device.id, "ehr_brand_location": location.id}
    )
    again = api_client.post(
        "/api/v1/fhir_sources",
        {"patient_id": patient.id, "data_source": device.id, "ehr_base_url": brand.fhir_base_url},
    )
    assert first.status_code == 201 and again.status_code == 200, again.content
    assert again.json()["id"] == first.json()["id"]


# -- uploading under it --


def test_practitioner_uploads_under_the_source_they_registered(api_client, patient, device):
    source_id = api_client.post("/api/v1/fhir_sources", {"patient_id": patient.id, "data_source": device.id}).json()[
        "id"
    ]
    r = api_client.post("/fhir-import/R4/Condition", _r4_condition(patient), HTTP_X_JHE_FHIR_SOURCE_ID=str(source_id))
    assert r.status_code == 200, r.content
    assert r.json()["entry"][0]["response"]["status"] == "201 Created", r.content
    row = FhirAuxResource.objects.get(fhir_source_id=source_id)
    assert row.fhir_source.patient_id == patient.id


def test_viewer_cannot_upload_under_a_patients_source(viewer, patient, device):
    source = FhirSource.objects.create(patient=patient, data_source=device, label="EHR")
    client = _client(viewer)
    r4 = client.post("/fhir-import/R4/Condition", _r4_condition(patient), HTTP_X_JHE_FHIR_SOURCE_ID=str(source.id))
    assert r4.status_code == 403, r4.content
    # These Condition fields are the same in R4 and R5, so the body serves the R5 endpoint too.
    r5 = client.post("/FHIR/R5/Condition", _r4_condition(patient), HTTP_X_JHE_FHIR_SOURCE_ID=str(source.id))
    assert r5.status_code == 403, r5.content
    assert not FhirAuxResource.objects.filter(fhir_source=source).exists()


# -- reading and managing sources --


def test_practitioner_lists_only_their_organizations_patients_sources(api_client, patient, stranger, device):
    mine = FhirSource.objects.create(patient=patient, data_source=device, label="EHR")
    r = api_client.get("/api/v1/fhir_sources")
    assert r.status_code == 200, r.content
    assert [source["id"] for source in r.json()["results"]] == [mine.id]
    stranger_source = FhirSource.objects.get(patient=stranger)
    assert api_client.get(f"/api/v1/fhir_sources/{stranger_source.id}").status_code == 404


def test_viewer_can_read_but_not_change_a_source(viewer, patient, device):
    source = FhirSource.objects.create(patient=patient, data_source=device, label="EHR")
    client = _client(viewer)
    assert client.get(f"/api/v1/fhir_sources/{source.id}").status_code == 200
    assert client.patch(f"/api/v1/fhir_sources/{source.id}", {"label": "Renamed"}).status_code == 403
    assert client.delete(f"/api/v1/fhir_sources/{source.id}").status_code == 403
    assert client.post(f"/api/v1/fhir_sources/{source.id}/index_refs").status_code == 403
    source.refresh_from_db()
    assert source.label == "EHR"


def test_practitioner_with_manage_data_can_change_a_source_but_not_its_patient(api_client, patient, device):
    source = FhirSource.objects.create(patient=patient, data_source=device, label="EHR")
    assert api_client.patch(f"/api/v1/fhir_sources/{source.id}", {"label": "Renamed"}).status_code == 200
    moved = api_client.patch(f"/api/v1/fhir_sources/{source.id}", {"patient_id": patient.id + 1})
    assert moved.status_code == 400, moved.content
    source.refresh_from_db()
    assert source.label == "Renamed" and source.patient_id == patient.id
    assert api_client.post(f"/api/v1/fhir_sources/{source.id}/index_refs").status_code == 200
    assert api_client.delete(f"/api/v1/fhir_sources/{source.id}").status_code == 204
