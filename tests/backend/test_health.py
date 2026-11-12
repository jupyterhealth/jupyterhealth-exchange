"""
Unit tests for the ``/health`` view — run locally with the Django test client
(no ``--smoke-url`` required).  Live-deployment smoke coverage lives in
``tests/smoke/test_smoke.py``.
"""

import pytest


@pytest.mark.django_db
class TestHealthViewUnit:
    """Unit tests for the ``/health`` view — runs locally without ``--smoke-url``."""

    def test_health_returns_200_json(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_health_version_matches_settings(self, client):
        from django.conf import settings

        resp = client.get("/health")
        data = resp.json()
        assert data["version"] == settings.JHE_VERSION

    def test_health_unauthenticated(self, client):
        """``/health`` must work without any authentication."""
        # client fixture is unauthenticated by default
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_content_type(self, client):
        resp = client.get("/health")
        assert resp["Content-Type"] == "application/json"


class TestStrictTransportSecurity:
    """``SECURE_HSTS_SECONDS`` is a deployment setting, off by default. Probed on a public,
    database-free path."""

    PATH = "/.well-known/assetlinks.json"

    def test_off_by_default(self, client, settings):
        assert settings.SECURE_HSTS_SECONDS == 0
        assert "Strict-Transport-Security" not in client.get(self.PATH, secure=True)

    def test_sent_on_https_when_set(self, client, settings):
        settings.SECURE_HSTS_SECONDS = 31536000
        settings.SECURE_HSTS_INCLUDE_SUBDOMAINS = False
        assert client.get(self.PATH, secure=True)["Strict-Transport-Security"] == "max-age=31536000"
        assert "Strict-Transport-Security" not in client.get(self.PATH, secure=False)

    def test_behind_the_load_balancer_the_forwarded_proto_counts(self, client, settings):
        settings.SECURE_HSTS_SECONDS = 3600
        resp = client.get(self.PATH, HTTP_X_FORWARDED_PROTO="https")
        assert resp["Strict-Transport-Security"] == "max-age=3600"
