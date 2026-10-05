import base64

from oauth2_provider.models import get_application_model
from rest_framework.test import APIClient

from core.models import JheClient, JheUser, PractitionerClient

from .utils import fetch_paginated

Application = get_application_model()


def _practitioner_client(user):
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(user)
    return api_client


def _make_patient(email="pc-patient@example.org"):
    return JheUser.objects.create_user(
        email=email,
        password="testpass123",
        user_type="patient",
    )


def _decode_key(key):
    return base64.b64decode(key).decode().split(":", 1)


def test_create_returns_api_key_and_links_practitioner(user):
    api_client = _practitioner_client(user)

    r = api_client.post("/api/v1/practitioner_clients", {"label": "my laptop"}, format="json")

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["label"] == "my laptop"
    assert body["name"].startswith(f"_practitioner_client_{user.practitioner.id}_")
    assert body["key"]

    pc = PractitionerClient.objects.get(id=body["id"])
    assert pc.practitioner_id == user.practitioner.id

    # key is base64(client_id:client_secret) of the new confidential client-credentials app
    client_id, client_secret = _decode_key(body["key"])
    app = pc.application
    assert client_id == app.client_id
    assert client_secret == app.client_secret
    assert app.client_type == Application.CLIENT_CONFIDENTIAL
    assert app.authorization_grant_type == Application.GRANT_CLIENT_CREDENTIALS
    assert app.user_id == user.id
    # no patient JheClient is created for a practitioner (client-credentials) app
    assert not JheClient.objects.filter(application=app).exists()


def test_create_label_optional(user):
    api_client = _practitioner_client(user)
    r = api_client.post("/api/v1/practitioner_clients", {}, format="json")
    assert r.status_code == 201, r.text
    assert r.json()["label"] == ""


def test_create_forbidden_for_patient(organization):
    patient_user = _make_patient()
    api_client = _practitioner_client(patient_user)
    r = api_client.post("/api/v1/practitioner_clients", {"label": "x"}, format="json")
    assert r.status_code == 403, r.text


def test_create_requires_auth():
    api_client = APIClient()
    r = api_client.post("/api/v1/practitioner_clients", {"label": "x"}, format="json")
    assert r.status_code == 401


def test_list_and_read_only_own_clients(user):
    api_client = _practitioner_client(user)
    created = api_client.post("/api/v1/practitioner_clients", {"label": "a"}, format="json").json()

    # a second practitioner with their own client
    other = JheUser.objects.create_user(email="pc-other@example.org", password="x", user_type="practitioner")
    _practitioner_client(other).post("/api/v1/practitioner_clients", {"label": "b"}, format="json")

    listed = fetch_paginated(api_client, "/api/v1/practitioner_clients")
    ids = {c["id"] for c in listed}
    assert created["id"] in ids
    assert len(ids) == 1  # only own client

    r = api_client.get(f"/api/v1/practitioner_clients/{created['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["key"]


def test_cannot_read_another_practitioners_client(user):
    other = JheUser.objects.create_user(email="pc-other2@example.org", password="x", user_type="practitioner")
    other_client = (
        _practitioner_client(other).post("/api/v1/practitioner_clients", {"label": "b"}, format="json").json()
    )

    r = _practitioner_client(user).get(f"/api/v1/practitioner_clients/{other_client['id']}")
    assert r.status_code == 404


def test_update_only_label(user):
    api_client = _practitioner_client(user)
    created = api_client.post("/api/v1/practitioner_clients", {"label": "before"}, format="json").json()
    original_name = created["name"]

    r = api_client.patch(
        f"/api/v1/practitioner_clients/{created['id']}",
        {"label": "after", "name": "hacked", "key": "hacked"},
        format="json",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "after"
    assert body["name"] == original_name  # name (and everything else) is immutable


def test_put_not_allowed(user):
    api_client = _practitioner_client(user)
    created = api_client.post("/api/v1/practitioner_clients", {"label": "x"}, format="json").json()
    r = api_client.put(f"/api/v1/practitioner_clients/{created['id']}", {"label": "y"}, format="json")
    assert r.status_code == 405


def test_delete_removes_application_and_client(user):
    api_client = _practitioner_client(user)
    created = api_client.post("/api/v1/practitioner_clients", {"label": "x"}, format="json").json()
    pc = PractitionerClient.objects.get(id=created["id"])
    app_id = pc.application_id

    r = api_client.delete(f"/api/v1/practitioner_clients/{created['id']}")
    assert r.status_code in (200, 204), r.text
    assert not PractitionerClient.objects.filter(id=created["id"]).exists()
    assert not Application.objects.filter(id=app_id).exists()


def test_practitioner_client_excluded_from_admin_clients(user, superuser):
    # The admin Clients endpoint manages patient (authorization-code) clients; practitioner
    # client-credentials apps must not appear there.
    created = _practitioner_client(user).post("/api/v1/practitioner_clients", {"label": "x"}, format="json").json()
    pc = PractitionerClient.objects.get(id=created["id"])

    listed = fetch_paginated(_practitioner_client(superuser), "/api/v1/clients")
    assert pc.application_id not in {c["id"] for c in listed}


def test_cannot_delete_another_practitioners_client(user):
    other = JheUser.objects.create_user(email="pc-other3@example.org", password="x", user_type="practitioner")
    other_client = (
        _practitioner_client(other).post("/api/v1/practitioner_clients", {"label": "b"}, format="json").json()
    )

    r = _practitioner_client(user).delete(f"/api/v1/practitioner_clients/{other_client['id']}")
    assert r.status_code == 404
    assert PractitionerClient.objects.filter(id=other_client["id"]).exists()
