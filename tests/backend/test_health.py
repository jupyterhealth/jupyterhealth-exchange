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
