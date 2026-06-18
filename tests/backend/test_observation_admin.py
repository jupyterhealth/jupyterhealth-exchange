"""
Issue #527: saving an Observation whose omh_data fails OMH schema validation must
re-render the admin form with an inline error on the omh_data field, not raise a 500.
"""

import json

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from core.models import CodeableConcept, Observation

JheUser = get_user_model()


@pytest.fixture
def admin_client(db, client):
    su = JheUser.objects.create_superuser(email="admin@example.org", password="pw")
    client.force_login(su)
    return client


@pytest.fixture
def patient(db):
    user = JheUser.objects.create_user(email="obs-patient@example.org", password="x", user_type="patient")
    return user.patient


@pytest.fixture
def heart_rate_concept(db):
    concept, _ = CodeableConcept.objects.update_or_create(
        coding_system="https://w3id.org/openmhealth",
        coding_code="omh:heart-rate:2.0",
        text="omh:heart-rate:2.0",
    )
    return concept


def test_admin_add_invalid_omh_data_renders_field_error_not_500(admin_client, patient, heart_rate_concept):
    url = reverse("admin:core_observation_add")
    # Valid JSON, but the header/body fail the OMH schema -> Observation.clean() raises.
    r = admin_client.post(
        url,
        {
            "subject_patient": patient.id,
            "codeable_concept": heart_rate_concept.id,
            "omh_data": json.dumps({"header": {}, "body": {}}),
            "status": "final",
        },
    )
    # Form re-renders (200), nothing is saved, and the error is keyed to the omh_data field.
    assert r.status_code == 200
    assert Observation.objects.count() == 0
    assert "omh_data" in r.context["adminform"].form.errors


def test_admin_observation_data_source_is_optional(db):
    # Issue #603: data_source is nullable in the DB and usually left null, so the admin
    # Add/Change form must not mark it required.
    request = RequestFactory().get(reverse("admin:core_observation_add"))
    form_class = admin.site._registry[Observation].get_form(request)
    assert form_class.base_fields["data_source"].required is False
