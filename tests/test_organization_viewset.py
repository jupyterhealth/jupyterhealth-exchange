import pytest
from rest_framework.test import APIClient

from core.models import JheUser, Organization

from .utils import fetch_paginated


# TODO: refine organization permissions for non-members
# ref: https://github.com/jupyterhealth/jupyterhealth-exchange/issues/270
def test_list_organizations(api_client, organization):
    for i in range(10):
        Organization.objects.create(
            name=f"Test org {i}",
            type="other",
        )
    orgs = fetch_paginated(api_client, "/api/v1/organizations", {"pageSize": 2})
    assert len(orgs) == Organization.objects.all().count()


def test_create_delete_organization(api_client, organization):
    to_create = {"part_of": organization.id, "type": "edu", "name": "some org"}
    r = api_client.post("/api/v1/organizations", to_create)
    assert r.status_code == 201, r.text
    info = r.json()
    # FIXME: currentUserRole is wrong
    # assert info["currentUserRole"] == "manager"

    org_id = info["id"]
    r = api_client.get(f"/api/v1/organizations/{org_id}")
    assert r.status_code == 200, r.text
    # fails: currentUserRole is wrong on POST
    # assert r.json() == info
    info2 = r.json()
    assert info2["name"] == info["name"]
    assert info2["currentUserRole"] == "manager"
    r = api_client.delete(f"/api/v1/organizations/{org_id}")
    assert r.status_code == 204, r.text


def test_create_invalid_organization(api_client, organization):
    r = api_client.post("/api/v1/organizations", {})
    assert r.status_code == 403
    r = api_client.post("/api/v1/organizations", {"part_of": 999})
    assert r.status_code == 403


def test_patch_org(api_client, organization):
    before = api_client.get(f"/api/v1/organizations/{organization.id}").json()
    r = api_client.patch(f"/api/v1/organizations/{organization.id}", {"name": "new name"})
    assert r.status_code == 200
    after = r.json()
    assert after["name"] == "new name"
    assert after["id"] == before["id"]


# @pytest.mark.xfail(reason="")
def test_get_organization_types(api_client):
    r = api_client.get("/api/v1/organizations/types")
    assert r.status_code == 200, r.text
    types = r.json()
    assert types == Organization.ORGANIZATION_TYPES


def test_get_organization_tree(api_client, organization):
    sub_org = Organization.objects.create(
        part_of=organization,
        name="sub",
        type="other",
    )
    r = api_client.get(f"/api/v1/organizations/{organization.id}/tree")
    assert r.status_code == 200, r.text
    tree = r.json()
    assert tree["id"] == organization.id
    assert tree["children"][0]["id"] == sub_org.id
    # TODO: more sub-orgs, root org


# FIXME: org/users should be paginated
def test_get_organization_users(api_client, user, organization):
    r = api_client.get(f"/api/v1/organizations/{organization.id}/users")
    assert r.status_code == 200, r.text
    users = r.json()
    print(users)
    assert users
    assert users[0]["id"] == user.id


def test_add_remove_organization_users(api_client, user, organization):
    # FIXME: get/post/delete all have different endpoints,
    # but they should all be the same (`$org/users`)
    user_url = f"/api/v1/organizations/{organization.id}/user"
    users_url = user_url + "s"
    user2 = JheUser.objects.create_user(
        email="user2@example.org",
        user_type="practitioner",
    )
    client2 = APIClient()
    client2.default_format = "json"
    client2.force_authenticate(user2)
    r = api_client.get(users_url)
    assert r.status_code == 200, r.text
    users = r.json()
    assert len(users) == 1

    r = api_client.post(
        user_url,
        {
            "jhe_user_id": user2.id,
            "organization_partitioner_role": "viewer",
        },
    )
    assert r.status_code == 200, r.text
    response = r.json()
    assert response["role"] == "viewer"
    assert response["practitioner"]["id"] == user2.practitioner.id

    r = api_client.get(users_url)
    users = r.json()
    assert len(users) == 2

    # try to promote myself, should fail
    r = client2.post(
        user_url,
        {
            "jhe_user_id": user2.id,
            "organization_partitioner_role": "manager",
        },
    )
    assert r.status_code == 403, r.text

    # FIXME: modifying role doesn't work because
    # it tries to create duplicate record;
    # have to remove and re-add
    # r = api_client.post(user_url, {
    #     "jhe_user_id": user2.id,
    #     "organization_partitioner_role": "manager",
    # })
    # assert r.status_code == 200, r.text
    # response = r.json()
    # assert response["role"] == "manager"
    # assert response["practitioner"]["id"] == user2.id

    r = api_client.delete(
        f"/api/v1/organizations/{organization.id}/remove_user",
        {
            "jhe_user_id": user2.id,
        },
    )
    assert r.status_code == 204, r.text

    r = api_client.get(users_url)
    users = r.json()
    assert len(users) == 1

    # add a patient
    # FIXME: adding patient on this endpoint doesn't work,
    # but it's partially implemented
    # should the implementation be removed? It's redundant
    # patient = add_patients(1)[0]
    # r = api_client.post(user_url, {
    #     # FIXME: argument type doesn't match input
    #     # (it's patient id, not user id)
    #     "jhe_user_id": patient.id,
    #     "user_type": "patient",
    # })
    # assert r.status_code == 200, r.text
    # assert r.json() == {}


#
# r = api_client.get(users_url)
# users = r.json()
# assert len(users) == 2


def test_add_user_invalid(api_client, organization):
    pytest.skip("TODO")
    # should exercise:
    # - missing user id
    # - nonexistent user id
    # - missing role
    # - invalid role


def test_get_organization_studies(api_client, organization, hr_study):
    r = api_client.get(f"/api/v1/organizations/{organization.id}/studies")
    assert r.status_code == 200, r.text
    studies = r.json()
    assert studies[0]["id"] == hr_study.id
