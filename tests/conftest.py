"""
pytest configuration and fixtures
"""

import pytest
from rest_framework.test import APIClient

from core.models import (
    DataSource,
    JheUser,
    Organization,
    PractitionerOrganization,
)

from .utils import (
    Code,
    add_patient_to_study,
    create_study,
)


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Org", type="other")


@pytest.fixture
def superuser(db):
    return JheUser.objects.create_superuser(
        email="superuser@example.org",
        password="unused",
    )


@pytest.fixture
def user(organization):
    user = JheUser.objects.create_user(
        email="test-user@example.org",
        password="testpass123",
        identifier="test-practitioner",
        user_type="practitioner",
    )
    PractitionerOrganization.objects.create(
        practitioner=user.practitioner,
        organization=organization,
        role="manager",
    )
    return user


@pytest.fixture
def device(db):
    return DataSource.objects.create(name="test device")


@pytest.fixture
def api_client(user):
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(user)
    return api_client


@pytest.fixture
def patient(organization):
    user = JheUser.objects.create_user(
        email="test-patient@example.org",
        password="testpass123",
        identifier="test-patient",
        user_type="patient",
    )
    user.patient.organizations.add(organization)
    return user.patient


@pytest.fixture
def hr_study(organization, user, patient):
    study = create_study(
        organization=organization,
        codes=[Code.HeartRate],
    )
    add_patient_to_study(patient=patient, study=study)
    return study
