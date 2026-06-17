"""Issue #525: the Django admin Observations changelist shows the patient's JHE User ID."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from .utils import Code, add_observations

JheUser = get_user_model()


@pytest.fixture
def admin_client(db, client):
    su = JheUser.objects.create_superuser(email="admin-525@example.org", password="pw")
    client.force_login(su)
    return client


def test_observation_changelist_shows_user_id(admin_client, patient):
    add_observations(patient=patient, code=Code.HeartRate, n=2)
    url = reverse("admin:core_observation_changelist")
    r = admin_client.get(url)
    assert r.status_code == 200
    # the "User ID" column header and the patient's jhe_user_id are both rendered
    assert b"User ID" in r.content
    assert str(patient.jhe_user_id).encode() in r.content
