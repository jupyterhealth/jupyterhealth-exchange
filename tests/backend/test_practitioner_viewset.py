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

    assert r.status_code == 204, r.text
    assert not Practitioner.objects.filter(id=practitioner.id).exists()
    assert not JheUser.objects.filter(email="orphan-prac@example.org").exists()
    # email is now reusable
    JheUser.objects.create_user(email="orphan-prac@example.org", password="x", user_type="practitioner")
    assert JheUser.objects.filter(email="orphan-prac@example.org").count() == 1


def test_delete_practitioner_preserves_superuser(superuser):
    user, practitioner = _make_practitioner("admin-prac@example.org", is_superuser=True, is_staff=True)
    api_client = _superuser_client(superuser)

    r = api_client.delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code == 204, r.text
    assert not Practitioner.objects.filter(id=practitioner.id).exists()
    # superuser login is preserved
    assert JheUser.objects.filter(email="admin-prac@example.org").exists()


def test_delete_practitioner_preserves_user_with_patient_profile(superuser):
    user, practitioner = _make_practitioner("dual-role@example.org")
    Patient.objects.create(jhe_user=user, name_family="Last", name_given="First", birth_date="2000-01-01")
    api_client = _superuser_client(superuser)

    r = api_client.delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code == 204, r.text
    assert not Practitioner.objects.filter(id=practitioner.id).exists()
    # user kept because a Patient profile still references it
    assert JheUser.objects.filter(email="dual-role@example.org").exists()


def test_list_practitioners(superuser):
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(superuser)
    practitioners = fetch_paginated(api_client, "/api/v1/practitioners")
    assert len(practitioners) >= 0


def test_list_practitioners_pagination_is_ordered(superuser, recwarn):
    # The practitioner list is paginated, so its queryset must have a stable order;
    # otherwise DRF emits an UnorderedObjectListWarning and pages can skip/repeat rows (issue #589).
    for i in range(15):
        _make_practitioner(f"prac-order-{i:02d}@example.org")
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(superuser)
    r = api_client.get("/api/v1/practitioners", {"pageSize": 10})
    assert r.status_code == 200, r.text
    unordered = [w for w in recwarn.list if w.category.__name__ == "UnorderedObjectListWarning"]
    assert not unordered, [str(w.message) for w in unordered]
    ids = [row["id"] for row in r.json()["results"]]
    assert ids == sorted(ids)


def test_list_practitioners_no_auth(user):
    api_client = APIClient()
    r = api_client.get("/api/v1/practitioners")
    assert r.status_code == 401


def test_list_practitioners_non_superuser(api_client):
    r = api_client.get("/api/v1/practitioners")
    assert r.status_code == 403


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
    assert r.status_code == 201, r.text
    info = r.json()
    assert "id" in info
    assert info["telecomEmail"] == email
    assert info["organizations"]
    assert info["organizations"][0]["id"] == organization.id
    r = api_client.get(f"/api/v1/practitioners/{info['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == info

    r = api_client.delete(f"/api/v1/practitioners/{info['id']}?organization_id={organization.id}")
    assert r.status_code == 204, r.text


def test_update_practitioner_name(superuser):
    # Issue #586: editing a practitioner's first/last name in jhe-admin (PATCH) must persist.
    user, practitioner = _make_practitioner("update-prac@example.org")
    practitioner.name_family = "Old"
    practitioner.name_given = "Name"
    practitioner.save()
    api_client = _superuser_client(superuser)

    r = api_client.patch(
        f"/api/v1/practitioners/{practitioner.id}",
        {"nameFamily": "New", "nameGiven": "Person"},
        format="json",
    )

    assert r.status_code == 200, r.text
    practitioner.refresh_from_db()
    assert practitioner.name_family == "New"
    assert practitioner.name_given == "Person"


def test_update_practitioner_name_exact_frontend_request(superuser):
    # Reproduce the EXACT jhe-admin request: it appends ?organizationId=undefined
    # (no org selector on the Practitioners screen, so the JS value is the string "undefined").
    user, practitioner = _make_practitioner("update-prac2@example.org")
    practitioner.name_family = "Old"
    practitioner.name_given = "Name"
    practitioner.save()
    api_client = _superuser_client(superuser)

    r = api_client.patch(
        f"/api/v1/practitioners/{practitioner.id}?organizationId=undefined",
        {"nameFamily": "New", "nameGiven": "Person"},
        format="json",
    )

    assert r.status_code == 200, r.text
    practitioner.refresh_from_db()
    assert practitioner.name_family == "New"
    assert practitioner.name_given == "Person"


def test_create_invalid(superuser, organization):
    # creating a practitioner with no email is rejected up front (no orphan user/practitioner)
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
