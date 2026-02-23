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

# ---------------------------------------------------------------------------
# Smoke-test infrastructure
# ---------------------------------------------------------------------------


def pytest_addoption(parser):
    """Register the ``--smoke-url`` CLI option."""
    parser.addoption(
        "--smoke-url",
        action="store",
        default=None,
        help="Base URL of a running JHE instance for smoke tests, e.g. https://jhe.fly.dev",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip ``@pytest.mark.smoke`` tests when ``--smoke-url`` is not supplied."""
    smoke_url = config.getoption("--smoke-url")
    if smoke_url is not None:
        return  # URL provided — run them
    skip_smoke = pytest.mark.skip(reason="need --smoke-url to run smoke tests")
    for item in items:
        if "smoke" in item.keywords:
            item.add_marker(skip_smoke)


@pytest.fixture(scope="session")
def smoke_url(request):
    """The base URL supplied via ``--smoke-url``.  Trailing slash stripped."""
    url = request.config.getoption("--smoke-url")
    if url is None:
        pytest.skip("--smoke-url not provided")
    return url.rstrip("/")


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
