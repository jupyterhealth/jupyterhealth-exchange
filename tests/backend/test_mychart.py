"""Tests for the MyChart client: identifier proxy + page routes + seeded config."""

import pytest
from rest_framework.test import APIClient

from core.models import PatientIdentifier


@pytest.fixture
def patient_client(patient):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(patient.jhe_user)
    return client


def test_save_identifier_creates_patient_identifier(patient, patient_client):
    resp = patient_client.post(
        "/api/v1/mychart/identifier",
        {"system": "https://fhir.epic.com/.../R4", "value": "epic-abc-123"},
    )
    assert resp.status_code == 200
    assert PatientIdentifier.objects.filter(
        patient=patient, system="https://fhir.epic.com/.../R4", value="epic-abc-123"
    ).exists()


def test_save_identifier_is_idempotent(patient, patient_client):
    payload = {"system": "sys", "value": "v1"}
    patient_client.post("/api/v1/mychart/identifier", payload)
    patient_client.post("/api/v1/mychart/identifier", payload)
    assert PatientIdentifier.objects.filter(patient=patient, system="sys", value="v1").count() == 1


def test_save_identifier_does_not_clobber_other_identifiers(patient, patient_client):
    PatientIdentifier.objects.create(patient=patient, system="existing", value="keepme")
    patient_client.post("/api/v1/mychart/identifier", {"system": "sys", "value": "v1"})
    assert PatientIdentifier.objects.filter(patient=patient, system="existing", value="keepme").exists()


def test_save_identifier_requires_patient(db, user):
    # `user` fixture is a practitioner (no patient profile) -> 400
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(user)
    resp = client.post("/api/v1/mychart/identifier", {"system": "sys", "value": "v1"})
    assert resp.status_code == 400


def test_save_identifier_validates_body(patient, patient_client):
    resp = patient_client.post("/api/v1/mychart/identifier", {"system": "sys"})
    assert resp.status_code == 400


def test_connect_page_renders(db, client):
    # AllowAny page; renders even unauthenticated (JS handles auth).
    # `db` is needed because the view reads MyChart aux_data from the DB.
    resp = client.get("/clients/mychart/")
    assert resp.status_code == 200


def test_callback_page_renders(db, client):
    resp = client.get("/clients/mychart/callback")
    assert resp.status_code == 200
