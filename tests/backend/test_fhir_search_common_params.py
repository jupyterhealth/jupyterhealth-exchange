"""Issue #585 (first slice): resource-agnostic US Core search params on the FHIR endpoint -
`_id` (exact) and `_lastUpdated` with ge/le/gt/lt comparators (and ranges)."""

import datetime

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Observation

from .utils import Code, add_observations


@pytest.fixture
def api(user):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(user)  # practitioner who shares the patient's organization
    return client


def _bundle_ids(response):
    return {entry["resource"]["id"] for entry in response.json().get("entry", [])}


def _set_last_updated(obs_id, dt):
    # .update() bypasses auto_now so we can pin a known timestamp.
    Observation.objects.filter(id=obs_id).update(last_updated=dt)


def test_search_last_updated_ge(api, patient):
    add_observations(patient=patient, code=Code.HeartRate, n=2)
    old, new = sorted(Observation.objects.filter(subject_patient=patient), key=lambda o: o.id)
    _set_last_updated(old.id, timezone.make_aware(datetime.datetime(2020, 1, 1)))
    _set_last_updated(new.id, timezone.make_aware(datetime.datetime(2024, 6, 1)))

    r = api.get("/FHIR/R5/Observation", {"_lastUpdated": "ge2023-01-01"})

    assert r.status_code == 200, r.text
    ids = _bundle_ids(r)
    assert str(new.id) in ids
    assert str(old.id) not in ids


def test_search_last_updated_range(api, patient):
    add_observations(patient=patient, code=Code.HeartRate, n=3)
    a, b, c = sorted(Observation.objects.filter(subject_patient=patient), key=lambda o: o.id)
    _set_last_updated(a.id, timezone.make_aware(datetime.datetime(2019, 1, 1)))
    _set_last_updated(b.id, timezone.make_aware(datetime.datetime(2022, 6, 1)))
    _set_last_updated(c.id, timezone.make_aware(datetime.datetime(2025, 1, 1)))

    r = api.get("/FHIR/R5/Observation", {"_lastUpdated": ["ge2021-01-01", "le2023-01-01"]})

    assert r.status_code == 200, r.text
    assert _bundle_ids(r) == {str(b.id)}


def test_search_by_id(api, patient):
    add_observations(patient=patient, code=Code.HeartRate, n=2)
    target = Observation.objects.filter(subject_patient=patient).first()

    r = api.get("/FHIR/R5/Observation", {"_id": str(target.id)})

    assert r.status_code == 200, r.text
    assert _bundle_ids(r) == {str(target.id)}


def test_search_invalid_id_returns_empty_not_500(api, patient):
    add_observations(patient=patient, code=Code.HeartRate, n=1)

    r = api.get("/FHIR/R5/Observation", {"_id": "not-an-integer"})

    assert r.status_code == 200, r.text
    assert _bundle_ids(r) == set()


def test_search_invalid_last_updated_is_400(api, patient):
    add_observations(patient=patient, code=Code.HeartRate, n=1)

    r = api.get("/FHIR/R5/Observation", {"_lastUpdated": "ge-not-a-date"})

    assert r.status_code == 400, r.text
