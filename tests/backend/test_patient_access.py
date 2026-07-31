"""Tests for the Patient Access client: identifier proxy + page routes + seeded config."""

import pytest
from rest_framework.test import APIClient

from core.models import JheUser, PatientIdentifier


@pytest.fixture
def patient_client(patient):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(patient.jhe_user)
    return client


def test_save_identifier_creates_patient_identifier(patient, patient_client):
    resp = patient_client.post(
        "/api/v1/patient-access/identifier",
        {"system": "https://fhir.epic.com/.../R4", "value": "epic-abc-123"},
    )
    assert resp.status_code == 200
    assert PatientIdentifier.objects.filter(
        patient=patient, system="https://fhir.epic.com/.../R4", value="epic-abc-123"
    ).exists()


def test_save_identifier_is_idempotent(patient, patient_client):
    payload = {"system": "sys", "value": "v1"}
    patient_client.post("/api/v1/patient-access/identifier", payload)
    patient_client.post("/api/v1/patient-access/identifier", payload)
    assert PatientIdentifier.objects.filter(patient=patient, system="sys", value="v1").count() == 1


def test_save_identifier_does_not_clobber_other_identifiers(patient, patient_client):
    PatientIdentifier.objects.create(patient=patient, system="existing", value="keepme")
    patient_client.post("/api/v1/patient-access/identifier", {"system": "sys", "value": "v1"})
    assert PatientIdentifier.objects.filter(patient=patient, system="existing", value="keepme").exists()


def test_save_identifier_conflicts_when_owned_by_another_patient(organization, patient, patient_client):
    # (system, value) is globally unique. Without the conflict check the caller gets a 200
    # and believes the id was attached to them, while the row still points at someone else.
    other = JheUser.objects.create_user(
        email="other-patient@example.org",
        password="testpass123",
        identifier="other-patient",
        user_type="patient",
    ).patient
    other.organizations.add(organization)
    PatientIdentifier.objects.create(patient=other, system="sys", value="taken")

    resp = patient_client.post("/api/v1/patient-access/identifier", {"system": "sys", "value": "taken"})

    assert resp.status_code == 409
    assert PatientIdentifier.objects.get(system="sys", value="taken").patient_id == other.id
    assert not PatientIdentifier.objects.filter(patient=patient, system="sys", value="taken").exists()


def test_save_identifier_requires_patient(db, user):
    # `user` fixture is a practitioner (no patient profile) -> 400
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(user)
    resp = client.post("/api/v1/patient-access/identifier", {"system": "sys", "value": "v1"})
    assert resp.status_code == 400


def test_save_identifier_validates_body(patient, patient_client):
    resp = patient_client.post("/api/v1/patient-access/identifier", {"system": "sys"})
    assert resp.status_code == 400


def test_connect_page_renders(db, client):
    # AllowAny page; renders even unauthenticated (JS handles auth).
    # `db` is needed because the view reads Patient Access aux_data from the DB.
    resp = client.get("/clients/patient-access/")
    assert resp.status_code == 200


def test_callback_page_renders(db, client):
    resp = client.get("/clients/patient-access/callback")
    assert resp.status_code == 200


def test_connect_page_includes_hospital_picker(db, client):
    # The connect page must render the picker input and pass it to startPatientAccessConnect,
    # otherwise the patient can never choose a hospital.
    html = client.get("/clients/patient-access/").content.decode()
    assert 'id="hospital-search"' in html
    assert 'id="hospital-results"' in html
    assert "startPatientAccessConnect(out, PATIENT_ACCESS_CONFIG, picker)" in html
