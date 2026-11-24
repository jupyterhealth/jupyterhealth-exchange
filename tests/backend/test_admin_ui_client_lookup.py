"""The browser login's client id is looked up by application name and cached per process; a
lookup that finds nothing is not cached, so creating the application after the first request
takes effect on the next one."""

import pytest
from oauth2_provider.models import get_application_model

from core.context_processors import _get_oidc_client_id

Application = get_application_model()


@pytest.fixture(autouse=True)
def fresh_cache():
    _get_oidc_client_id.cache_clear()
    yield
    _get_oidc_client_id.cache_clear()


@pytest.mark.django_db
def test_missing_application_is_not_cached():
    assert _get_oidc_client_id() is None
    app = Application.objects.create(
        name="JHE Admin UI",
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    assert _get_oidc_client_id() == app.client_id, "found on the next call, no restart needed"


@pytest.mark.django_db
def test_found_id_is_cached():
    app = Application.objects.create(
        name="JHE Admin UI",
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    assert _get_oidc_client_id() == app.client_id
    app.delete()
    assert _get_oidc_client_id() == app.client_id, "cached for the process once found"
