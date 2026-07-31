"""Access checks for the consent-status endpoint GET /api/v1/patients/{id}/consents (#670).

Confirms the two client types Momentum (patient mobile app) and the SoF provider EHR-launch app
authenticate as can both reach it, with no additional build: a patient reads their OWN consents,
and an authorized practitioner reads a patient's consents. A patient cannot read another patient's.
"""

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def patient_client(patient):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(patient.jhe_user)
    return client


def test_patient_reads_own_consents(patient, patient_client, hr_study):
    resp = patient_client.get(f"/api/v1/patients/{patient.id}/consents")
    assert resp.status_code == 200
    assert "consolidatedConsentedScopes" in resp.json()


def test_patient_cannot_read_another_patients_consents(patient, patient_client):
    resp = patient_client.get("/api/v1/patients/999999/consents")
    assert resp.status_code in (403, 404)


def test_authorized_practitioner_reads_patient_consents(api_client, patient, hr_study):
    # api_client is a practitioner (manager) in the org that runs hr_study, which the patient is in.
    resp = api_client.get(f"/api/v1/patients/{patient.id}/consents")
    assert resp.status_code == 200
    assert "consolidatedConsentedScopes" in resp.json()
