import pytest
from oauth2_provider.models import get_application_model

from core.management.commands.seed import Command

Application = get_application_model()


@pytest.mark.django_db
def test_seeds_broker_app_with_skip_authorization_when_env_set(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "broker-client-id")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "broker-secret")

    Command().seed_mcp_broker_application()

    app = Application.objects.get(name="JHE MCP Server")
    assert app.client_id == "broker-client-id"
    assert app.client_type == "confidential"
    assert app.authorization_grant_type == "authorization-code"
    assert app.skip_authorization is True
    assert app.redirect_uris == "https://jhe-mcp.fly.dev/oauth/callback"


@pytest.mark.django_db
def test_skips_broker_app_when_env_missing(monkeypatch):
    monkeypatch.delenv("MCP_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MCP_OAUTH_CLIENT_SECRET", raising=False)

    Command().seed_mcp_broker_application()

    assert not Application.objects.filter(name="JHE MCP Server").exists()


@pytest.mark.django_db
def test_broker_redirect_uri_overridable_via_env(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "broker-client-id")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_SECRET", "broker-secret")
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URI", "https://example.test/oauth/callback")

    Command().seed_mcp_broker_application()

    app = Application.objects.get(name="JHE MCP Server")
    assert app.redirect_uris == "https://example.test/oauth/callback"
