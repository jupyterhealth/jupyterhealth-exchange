"""
Issue #533: the Django admin must be able to view and delete a JheUser even though the
groups / user_permissions M2M tables were dropped (migration 0011). Default ModelAdmin
machinery queries those dropped tables (`core_jheuser_groups`) and crashes.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

JheUser = get_user_model()


@pytest.fixture
def admin_client(db, client):
    su = JheUser.objects.create_superuser(email="admin@example.org", password="pw")
    client.force_login(su)
    return client


@pytest.fixture
def target(db):
    return JheUser.objects.create_user(email="target@ucsf.edu", password="x", user_type="practitioner")


def test_change_view_loads(admin_client, target):
    url = reverse("admin:core_jheuser_change", args=[target.id])
    r = admin_client.get(url)
    assert r.status_code == 200


def test_single_delete_confirm_and_execute(admin_client, target):
    url = reverse("admin:core_jheuser_delete", args=[target.id])
    # confirmation page must render (collector must not hit core_jheuser_groups)
    assert admin_client.get(url).status_code == 200
    # POST confirms the delete
    r = admin_client.post(url, {"post": "yes"})
    assert r.status_code in (200, 302)
    assert not JheUser.objects.filter(id=target.id).exists()


def test_bulk_delete_selected(admin_client, target):
    url = reverse("admin:core_jheuser_changelist")
    # step 1: choose action, get the confirmation page
    confirm = admin_client.post(url, {"action": "delete_selected", "_selected_action": [str(target.id)]})
    assert confirm.status_code == 200
    # step 2: confirm
    r = admin_client.post(
        url,
        {"action": "delete_selected", "_selected_action": [str(target.id)], "post": "yes"},
    )
    assert r.status_code in (200, 302)
    assert not JheUser.objects.filter(id=target.id).exists()
