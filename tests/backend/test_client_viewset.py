"""Issue #293: coverage for the multi-client support feature.

These exercise the admin-managed patient Clients (OAuth Applications wrapped by a
JheClient), their Study associations (StudyClient, so a study can have several clients),
the client data-source links, and the patient consolidated_clients view. The
client-credentials PractitionerClient feature (#596) is covered in
test_practitioner_client_viewset.py and is only checked here for list isolation.
"""

import pytest
from oauth2_provider.models import get_application_model
from rest_framework.test import APIClient

from core.models import ClientDataSource, JheClient, JheUser, PractitionerClient, StudyClient

from .utils import Code, add_patient_to_study, create_study, fetch_paginated

Application = get_application_model()

VALID_CLIENT = {
    "name": "Mobile App",
    "clientId": "mobile-app-client-id",
    "invitationUrl": "https://app.example.org/invite/CODE",
}


def _client_for(user):
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(user)
    return api_client


@pytest.fixture
def admin(superuser):
    return _client_for(superuser)


# --- create -----------------------------------------------------------------


def test_create_client_creates_public_app_and_jhe_client(admin):
    r = admin.post("/api/v1/clients", VALID_CLIENT, format="json")
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["name"] == VALID_CLIENT["name"]
    assert body["clientId"] == VALID_CLIENT["clientId"]
    assert body["invitationUrl"] == VALID_CLIENT["invitationUrl"]

    app = Application.objects.get(id=body["id"])
    assert app.client_type == Application.CLIENT_PUBLIC
    assert app.authorization_grant_type == Application.GRANT_AUTHORIZATION_CODE
    assert JheClient.objects.filter(application=app).exists()


def test_create_requires_name_client_id_and_invitation_url(admin):
    r = admin.post("/api/v1/clients", {"name": "only a name"}, format="json")
    assert r.status_code == 400, r.text
    body = r.json()
    assert "clientId" in body
    assert "invitationUrl" in body


def test_create_forbidden_for_non_superuser(api_client):
    # api_client is a practitioner manager; only super_user has client.manage.
    r = api_client.post("/api/v1/clients", VALID_CLIENT, format="json")
    assert r.status_code == 403, r.text


def test_create_requires_auth():
    r = APIClient().post("/api/v1/clients", VALID_CLIENT, format="json")
    assert r.status_code == 401


# --- list / isolation -------------------------------------------------------


def test_list_returns_only_patient_clients_not_practitioner_clients(admin, user):
    created = admin.post("/api/v1/clients", VALID_CLIENT, format="json").json()
    practitioner_client = (
        _client_for(user).post("/api/v1/practitioner_clients", {"label": "laptop"}, format="json").json()
    )

    listed = fetch_paginated(admin, "/api/v1/clients")
    ids = {c["id"] for c in listed}

    assert created["id"] in ids
    prac_app_id = PractitionerClient.objects.get(id=practitioner_client["id"]).application_id
    assert prac_app_id not in ids


# --- update / delete --------------------------------------------------------


def test_update_invitation_url(admin):
    created = admin.post("/api/v1/clients", VALID_CLIENT, format="json").json()
    new_url = "https://app.example.org/invite2/CODE"

    r = admin.patch(f"/api/v1/clients/{created['id']}", {"invitationUrl": new_url}, format="json")

    assert r.status_code == 200, r.text
    assert r.json()["invitationUrl"] == new_url
    assert JheClient.objects.get(application_id=created["id"]).invitation_url == new_url


def test_delete_removes_application_and_jhe_client(admin):
    created = admin.post("/api/v1/clients", VALID_CLIENT, format="json").json()

    r = admin.delete(f"/api/v1/clients/{created['id']}")

    assert r.status_code in (200, 204), r.text
    assert not Application.objects.filter(id=created["id"]).exists()
    assert not JheClient.objects.filter(application_id=created["id"]).exists()


# --- client data sources (POST 201 / DELETE 204 per #284) -------------------


def test_add_remove_client_data_sources(api_client, user, device):
    client_app = Application.objects.create(
        name="test client",
        user=user,
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    url = f"/api/v1/clients/{client_app.id}/data_sources"
    r = api_client.post(url, {"data_source_id": device.id})
    assert r.status_code == 201, r.text
    assert ClientDataSource.objects.filter(client=client_app, data_source=device).count() == 1

    r = api_client.delete(url, {"data_source_id": device.id})
    assert r.status_code == 204, r.text
    assert ClientDataSource.objects.filter(client=client_app, data_source=device).count() == 0


# --- Study <-> Client (multi-client per study) ------------------------------


def test_study_supports_multiple_clients(admin, organization):
    study = create_study(organization=organization, codes=[Code.HeartRate])
    c1 = admin.post("/api/v1/clients", {**VALID_CLIENT, "clientId": "c1"}, format="json").json()
    c2 = admin.post("/api/v1/clients", {**VALID_CLIENT, "name": "App 2", "clientId": "c2"}, format="json").json()

    for c in (c1, c2):
        r = admin.post(f"/api/v1/studies/{study.id}/clients", {"clientId": c["id"]}, format="json")
        assert r.status_code == 201, r.text

    listed = admin.get(f"/api/v1/studies/{study.id}/clients")
    assert listed.status_code == 200, listed.text
    # StudyClientSerializer uses depth=1, so `client` is the nested Application object.
    client_ids = {sc["client"]["id"] for sc in listed.json()}
    assert {c1["id"], c2["id"]} <= client_ids


# --- consolidated_clients (patient sees the clients of their studies) --------


def test_patient_consolidated_clients_lists_study_clients(admin, organization):
    study = create_study(organization=organization, codes=[Code.HeartRate])
    patient = JheUser.objects.create_user(email="cc-patient@example.org", password="x", user_type="patient").patient
    patient.organizations.add(organization)
    add_patient_to_study(patient=patient, study=study)

    client = admin.post("/api/v1/clients", {**VALID_CLIENT, "clientId": "cc1"}, format="json").json()
    StudyClient.objects.create(study=study, client_id=client["id"])

    r = admin.get(f"/api/v1/patients/{patient.id}/consolidated_clients")

    assert r.status_code == 200, r.text
    assert client["id"] in {c["id"] for c in r.json()}
