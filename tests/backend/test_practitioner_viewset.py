import pytest
from rest_framework.test import APIClient

from core.models import JheUser, Patient, Practitioner

from .utils import fetch_paginated


def _superuser_client(superuser):
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(superuser)
    return api_client


def _make_practitioner(email, **user_kwargs):
    user = JheUser.objects.create_user(
        email=email,
        password="testpass123",
        identifier=email,
        user_type="practitioner",
        **user_kwargs,
    )
    return user, user.practitioner


def test_delete_practitioner_removes_orphan_user(superuser):
    user, practitioner = _make_practitioner("orphan-prac@example.org")
    api_client = _superuser_client(superuser)

    r = api_client.delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code in (200, 204), r.text
    assert not Practitioner.objects.filter(id=practitioner.id).exists()
    assert not JheUser.objects.filter(email="orphan-prac@example.org").exists()
    # email is now reusable
    JheUser.objects.create_user(email="orphan-prac@example.org", password="x", user_type="practitioner")
    assert JheUser.objects.filter(email="orphan-prac@example.org").count() == 1


def test_delete_practitioner_preserves_superuser(superuser):
    user, practitioner = _make_practitioner("admin-prac@example.org", is_superuser=True, is_staff=True)
    api_client = _superuser_client(superuser)

    r = api_client.delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code in (200, 204), r.text
    assert not Practitioner.objects.filter(id=practitioner.id).exists()
    # superuser login is preserved
    assert JheUser.objects.filter(email="admin-prac@example.org").exists()


def test_delete_practitioner_preserves_user_with_patient_profile(superuser):
    user, practitioner = _make_practitioner("dual-role@example.org")
    Patient.objects.create(jhe_user=user, name_family="Last", name_given="First", birth_date="2000-01-01")
    api_client = _superuser_client(superuser)

    r = api_client.delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code in (200, 204), r.text
    assert not Practitioner.objects.filter(id=practitioner.id).exists()
    # user kept because a Patient profile still references it
    assert JheUser.objects.filter(email="dual-role@example.org").exists()


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


# def test_create_invalid(superuser, organization):
#     # TODO: fix - serializer crashes with AttributeError on jhe_user.email when practitioner has no user
#     api_client = APIClient()
#     api_client.default_format = "json"
#     api_client.force_authenticate(superuser)
#     r = api_client.post(
#         "/api/v1/practitioners",
#         {
#             "organizationId": organization.id,
#         },
#         format="json",
#     )
#     assert r.status_code == 400
