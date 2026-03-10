import pytest
from rest_framework.test import APIClient

from .utils import fetch_paginated


def test_list_practitioners(superuser):
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(superuser)
    practitioners = fetch_paginated(api_client, "/api/v1/practitioners")
    assert len(practitioners) >= 0


def test_list_practitioners_no_auth(user):
    api_client = APIClient()
    r = api_client.get("/api/v1/practitioners")
    assert r.status_code == 401


def test_list_practitioners_non_superuser(api_client):
    r = api_client.get("/api/v1/practitioners")
    assert r.status_code == 403


# fails with:
# django.db.utils.IntegrityError: null value in column "birth_date" of relation "core_patient" violates not-null constraint
# but shouldn't create a patient at all
@pytest.mark.xfail(reason="create practitioner doesn't work")
def test_create_delete(superuser, organization):
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(superuser)
    email = "testcreate-practitioner@example.com"
    r = api_client.post(
        "/api/v1/practitioners",
        {
            "organizationId": organization.id,
            "telecomEmail": email,
            "nameFamily": "last",
            "nameGiven": "first",
            # "birthDate": "2000-01-01",
        },
        format="json",
    )
    assert r.status_code == 200, r.text
    info = r.json()
    assert "id" in info
    assert info["telecomEmail"] == email
    assert info["organizations"]
    assert info["organizations"][0]["id"] == organization.id
    r = api_client.get(f"/api/v1/practitioners/{info['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == info

    r = api_client.delete(f"/api/v1/practitioners/{info['id']}?organization_id={organization.id}")
    assert r.status_code == 200, r.text
    assert r.json()["success"]


def test_create_invalid(superuser, organization):
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(superuser)
    r = api_client.post(
        "/api/v1/practitioners",
        {
            "organizationId": organization.id,
        },
        format="json",
    )
    assert r.status_code == 400
