"""Tests for the EHR Patient Portal client: identifier proxy + page routes + seeded config.

The client is served at /clients/ehr-patient-portal/, matching its Application and DataSource
rows. That path is registered as a redirect URI on the Epic app, so it and the Epic-side
registration have to move together. Its /api/v1/ endpoints moved with it.
"""

import pytest
from oauth2_provider.models import get_application_model
from rest_framework.test import APIClient

from core.models import ClientDataSource, DataSource, JheUser, PatientIdentifier


@pytest.fixture
def patient_client(patient):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(patient.jhe_user)
    return client


def test_save_identifier_creates_patient_identifier(patient, patient_client):
    resp = patient_client.post(
        "/api/v1/ehr-patient-portal/identifier",
        {"system": "https://fhir.epic.com/.../R4", "value": "epic-abc-123"},
    )
    assert resp.status_code == 200
    assert PatientIdentifier.objects.filter(
        patient=patient, system="https://fhir.epic.com/.../R4", value="epic-abc-123"
    ).exists()


def test_save_identifier_is_idempotent(patient, patient_client):
    payload = {"system": "sys", "value": "v1"}
    patient_client.post("/api/v1/ehr-patient-portal/identifier", payload)
    patient_client.post("/api/v1/ehr-patient-portal/identifier", payload)
    assert PatientIdentifier.objects.filter(patient=patient, system="sys", value="v1").count() == 1


def test_save_identifier_does_not_clobber_other_identifiers(patient, patient_client):
    PatientIdentifier.objects.create(patient=patient, system="existing", value="keepme")
    patient_client.post("/api/v1/ehr-patient-portal/identifier", {"system": "sys", "value": "v1"})
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

    resp = patient_client.post("/api/v1/ehr-patient-portal/identifier", {"system": "sys", "value": "taken"})

    assert resp.status_code == 409
    assert PatientIdentifier.objects.get(system="sys", value="taken").patient_id == other.id
    assert not PatientIdentifier.objects.filter(patient=patient, system="sys", value="taken").exists()


def test_save_identifier_requires_patient(db, user):
    # `user` fixture is a practitioner (no patient profile) -> 400
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(user)
    resp = client.post("/api/v1/ehr-patient-portal/identifier", {"system": "sys", "value": "v1"})
    assert resp.status_code == 400


def test_save_identifier_validates_body(patient, patient_client):
    resp = patient_client.post("/api/v1/ehr-patient-portal/identifier", {"system": "sys"})
    assert resp.status_code == 400


def test_connect_page_renders(db, client):
    # AllowAny page; renders even unauthenticated (JS handles auth).
    # `db` is needed because the view reads Patient Access aux_data from the DB.
    resp = client.get("/clients/ehr-patient-portal/")
    assert resp.status_code == 200


def test_callback_page_renders(db, client):
    resp = client.get("/clients/ehr-patient-portal/callback")
    assert resp.status_code == 200


def test_rename_migration_renames_both_rows_and_links_them(db):
    # Migration 0046: the deployed rows were named "Patient Access" / "Patient Access API",
    # which collided with the unrelated auth.patient_access_clients login-mode setting.
    import importlib

    from django.apps import apps as django_apps

    migration = importlib.import_module("core.migrations.0046_rename_ehr_patient_portal")

    app = get_application_model().objects.create(name="Patient Access", client_id="deployed-app-id")
    data_source = DataSource.objects.create(name="Patient Access API", type="medical_device")

    migration.rename_to_ehr_patient_portal(django_apps, None)

    app.refresh_from_db()
    data_source.refresh_from_db()
    assert app.name == "EHR Patient Portal"
    assert data_source.name == "EHR Patient Portal"
    assert data_source.type == "patient_app"
    # Renamed in place, so anything referencing them by FK still resolves.
    assert app.client_id == "deployed-app-id"
    assert ClientDataSource.objects.filter(client=app, data_source=data_source).exists()


def test_rename_migration_is_idempotent(db):
    # Replaying it (or running it on a database seeded after the rename shipped) must not
    # produce a second row under either name -- neither name column is unique.
    import importlib

    from django.apps import apps as django_apps

    migration = importlib.import_module("core.migrations.0046_rename_ehr_patient_portal")

    get_application_model().objects.create(name="Patient Access", client_id="deployed-app-id")
    DataSource.objects.create(name="Patient Access API", type="medical_device")

    migration.rename_to_ehr_patient_portal(django_apps, None)
    migration.rename_to_ehr_patient_portal(django_apps, None)

    assert get_application_model().objects.filter(name="EHR Patient Portal").count() == 1
    assert DataSource.objects.filter(name="EHR Patient Portal").count() == 1
    assert ClientDataSource.objects.count() == 1
