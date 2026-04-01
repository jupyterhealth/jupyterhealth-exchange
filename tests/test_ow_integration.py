"""
Tests for Open Wearables proxy endpoints (POST /api/v1/ow/users, GET /api/v1/ow/oauth/oura/authorize).
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from core.models import JheSetting, JheUser


@pytest.fixture
def ow_user(db):
    """A regular authenticated user for OW tests."""
    return JheUser.objects.create_user(
        email="ow-test@example.org",
        password="testpass123",
        identifier="",
        user_type="patient",
    )


@pytest.fixture
def ow_user_with_id(db):
    """A user who already has an OW identifier stored."""
    return JheUser.objects.create_user(
        email="ow-linked@example.org",
        password="testpass123",
        identifier="ow:550e8400-e29b-41d4-a716-446655440000",
        user_type="patient",
    )


@pytest.fixture
def ow_client(ow_user):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(ow_user)
    return client


@pytest.fixture
def ow_linked_client(ow_user_with_id):
    client = APIClient()
    client.default_format = "json"
    client.force_authenticate(ow_user_with_id)
    return client


@pytest.fixture
def anon_client():
    return APIClient()


@pytest.fixture
def ow_settings(db):
    """Create OW settings in JheSetting."""
    JheSetting.objects.create(
        key="ow.api_base_url",
        value_type="string",
        value_string="https://ow.example.com",
    )
    JheSetting.objects.create(
        key="ow.api_key",
        value_type="string",
        value_string="sk-test-api-key-12345678",
    )


# ============================================================================
# POST /api/v1/ow/users
# ============================================================================


class TestCreateOwUser:
    URL = "/api/v1/ow/users"

    def test_unauthenticated_returns_401(self, anon_client):
        resp = anon_client.post(self.URL)
        assert resp.status_code == 401

    def test_missing_ow_settings_returns_500(self, ow_client):
        resp = ow_client.post(self.URL)
        assert resp.status_code == 500
        assert "missing" in resp.json()["error"].lower()

    @patch("core.views.ow.requests.post")
    def test_creates_user_in_ow(self, mock_post, ow_client, ow_user, ow_settings):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "new-ow-user-id-123"}
        mock_post.return_value = mock_resp

        resp = ow_client.post(self.URL)

        assert resp.status_code == 201
        data = resp.json()
        assert data["owUserId"] == "new-ow-user-id-123"
        assert data["created"] is True

        # Verify OW API was called with correct payload
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[1]["json"]["email"] == "ow-test@example.org"
        assert "X-Open-Wearables-API-Key" in call_args[1]["headers"]

        # Verify user's identifier was updated
        ow_user.refresh_from_db()
        assert ow_user.identifier == "ow:new-ow-user-id-123"

    def test_returns_existing_ow_id_if_already_linked(self, ow_linked_client, ow_user_with_id, ow_settings):
        resp = ow_linked_client.post("/api/v1/ow/users")
        assert resp.status_code == 200
        data = resp.json()
        assert data["owUserId"] == "550e8400-e29b-41d4-a716-446655440000"
        assert data["created"] is False

    @patch("core.views.ow.requests.get")
    @patch("core.views.ow.requests.post")
    def test_handles_409_conflict_with_lookup(self, mock_post, mock_get, ow_client, ow_user, ow_settings):
        # OW returns 409 (user exists)
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 409
        mock_post.return_value = mock_post_resp

        # Lookup by email succeeds
        mock_get_resp = MagicMock()
        mock_get_resp.ok = True
        mock_get_resp.json.return_value = {"items": [{"id": "existing-ow-id"}]}
        mock_get.return_value = mock_get_resp

        resp = ow_client.post(self.URL)
        assert resp.status_code == 200
        data = resp.json()
        assert data["owUserId"] == "existing-ow-id"
        assert data["created"] is False

        ow_user.refresh_from_db()
        assert ow_user.identifier == "ow:existing-ow-id"

    @patch("core.views.ow.requests.post")
    def test_handles_ow_api_error(self, mock_post, ow_client, ow_settings):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        resp = ow_client.post(self.URL)
        assert resp.status_code == 502

    @patch("core.views.ow.requests.post")
    def test_handles_connection_error(self, mock_post, ow_client, ow_settings):
        import requests as req
        mock_post.side_effect = req.ConnectionError("Connection refused")

        resp = ow_client.post(self.URL)
        assert resp.status_code == 502
        assert "Failed to connect" in resp.json()["error"]


# ============================================================================
# GET /api/v1/ow/oauth/oura/authorize
# ============================================================================


class TestOuraAuthorize:
    URL = "/api/v1/ow/oauth/oura/authorize"

    def test_unauthenticated_returns_401(self, anon_client):
        resp = anon_client.get(self.URL)
        assert resp.status_code == 401

    def test_no_ow_user_returns_400(self, ow_client):
        resp = ow_client.get(self.URL)
        assert resp.status_code == 400
        assert "no open wearables account" in resp.json()["error"].lower()

    def test_missing_ow_settings_returns_500(self, ow_linked_client):
        resp = ow_linked_client.get(self.URL)
        assert resp.status_code == 500
        assert "missing" in resp.json()["error"].lower()

    @patch("core.views.ow.requests.get")
    def test_returns_authorization_url(self, mock_get, ow_linked_client, ow_settings):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "authorization_url": "https://cloud.ouraring.com/oauth/authorize?client_id=abc&state=xyz",
            "state": "xyz",
        }
        mock_get.return_value = mock_resp

        resp = ow_linked_client.get(self.URL)
        assert resp.status_code == 200
        data = resp.json()
        assert "authorizationUrl" in data
        assert "ouraring.com" in data["authorizationUrl"]

        # Verify correct params passed to OW
        call_args = mock_get.call_args
        assert call_args[1]["params"]["user_id"] == "550e8400-e29b-41d4-a716-446655440000"

    @patch("core.views.ow.requests.get")
    def test_uses_custom_redirect_uri(self, mock_get, ow_linked_client, ow_settings):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"authorization_url": "https://oura.example.com", "state": "abc"}
        mock_get.return_value = mock_resp

        resp = ow_linked_client.get(f"{self.URL}?redirect_uri=https://myapp.com/callback")
        assert resp.status_code == 200

        call_args = mock_get.call_args
        assert call_args[1]["params"]["redirect_uri"] == "https://myapp.com/callback"

    @patch("core.views.ow.requests.get")
    def test_handles_ow_api_error(self, mock_get, ow_linked_client, ow_settings):
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_get.return_value = mock_resp

        resp = ow_linked_client.get(self.URL)
        assert resp.status_code == 502

    @patch("core.views.ow.requests.get")
    def test_handles_connection_error(self, mock_get, ow_linked_client, ow_settings):
        import requests as req
        mock_get.side_effect = req.ConnectionError("Connection refused")

        resp = ow_linked_client.get(self.URL)
        assert resp.status_code == 502


# ============================================================================
# OW Settings helper
# ============================================================================


class TestOwConfig:
    def test_returns_error_when_no_settings(self, ow_client):
        """Both endpoints should fail gracefully when OW settings are missing."""
        resp = ow_client.post("/api/v1/ow/users")
        assert resp.status_code == 500
        assert "ow.api_base_url" in resp.json()["error"] or "missing" in resp.json()["error"].lower()

    def test_returns_error_when_partial_settings(self, db, ow_client):
        """Only one of the two required settings is configured."""
        JheSetting.objects.create(
            key="ow.api_base_url",
            value_type="string",
            value_string="https://ow.example.com",
        )
        resp = ow_client.post("/api/v1/ow/users")
        assert resp.status_code == 500
