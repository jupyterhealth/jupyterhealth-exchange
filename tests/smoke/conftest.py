"""
pytest configuration for live-deployment smoke tests.

This conftest is intentionally free of any Django / DRF imports so the smoke
suite can run in a minimal ``pip install pytest requests`` environment against a
deployed JHE instance (see ``fly_dev.yml`` and ``smoke_test.yml``).
"""

import pytest


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
