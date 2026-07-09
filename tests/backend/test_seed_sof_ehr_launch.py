import pytest
from oauth2_provider.models import get_application_model

from core.management.commands.seed import Command
from core.oauth2_validators import JheOAuth2Validator

Application = get_application_model()


def _secret_ok(app, plaintext):
    """Verify a plaintext secret against the stored (hashed) client_secret."""
    return JheOAuth2Validator()._check_secret(plaintext, app.client_secret)


@pytest.mark.django_db
def test_seeds_sof_ehr_launch_confidential_client(monkeypatch):
    monkeypatch.delenv("SOF_EHR_LAUNCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("SOF_EHR_LAUNCH_CLIENT_SECRET", raising=False)

    Command().seed_sof_ehr_launch_application()

    app = Application.objects.get(name="SoF EHR Launch")
    assert app.client_id == "sof-ehr-launch"
    assert app.client_type == "confidential"
    assert app.authorization_grant_type == "client-credentials"
    assert app.skip_authorization is True
    # secret is hashed at rest; the plaintext must still verify.
    assert app.client_secret != "sof-ehr-launch-dev-secret"
    assert _secret_ok(app, "sof-ehr-launch-dev-secret")


@pytest.mark.django_db
def test_sof_ehr_launch_credentials_overridable_via_env(monkeypatch):
    monkeypatch.setenv("SOF_EHR_LAUNCH_CLIENT_ID", "custom-sof-id")
    monkeypatch.setenv("SOF_EHR_LAUNCH_CLIENT_SECRET", "custom-sof-secret")

    Command().seed_sof_ehr_launch_application()

    app = Application.objects.get(name="SoF EHR Launch")
    assert app.client_id == "custom-sof-id"
    assert _secret_ok(app, "custom-sof-secret")


@pytest.mark.django_db
def test_seed_sof_ehr_launch_is_idempotent(monkeypatch):
    monkeypatch.delenv("SOF_EHR_LAUNCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("SOF_EHR_LAUNCH_CLIENT_SECRET", raising=False)

    Command().seed_sof_ehr_launch_application()
    Command().seed_sof_ehr_launch_application()

    assert Application.objects.filter(name="SoF EHR Launch").count() == 1
