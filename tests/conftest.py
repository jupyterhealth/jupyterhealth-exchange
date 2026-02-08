"""
pytest configuration and fixtures
"""

import pytest
from rest_framework.test import APIClient

from core.models import (
    DataSource,
    JheUser,
    Organization,
)


from .utils import (
    add_patient_to_study,
    create_study,
    Code,
)


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Org", type="other")


@pytest.fixture
def user(organization):
    user = JheUser.objects.create_user(
        email="test-user@example.org",
        password="testpass123",
        identifier="test-practitioner",
        user_type="practitioner",
    )
    user.practitioner.organizations.add(organization)
    return user


@pytest.fixture
def device(db):
    return DataSource.objects.create(name="test device")


@pytest.fixture
def client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


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
