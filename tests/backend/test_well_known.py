"""
Unit tests for the ``/.well-known/`` mobile app association files.

iOS Universal Links and Android App Links only work when the OS can fetch these
files over HTTPS, without authentication and without a redirect.  Live-deployment
coverage lives in ``tests/smoke/test_smoke.py``.
"""

import json

import pytest
from django.conf import settings

APPLE_PATH = "/.well-known/apple-app-site-association"
ANDROID_PATH = "/.well-known/assetlinks.json"


def _committed(filename):
    return (settings.BASE_DIR / "core" / "data" / filename).read_bytes()


class TestAppleAppSiteAssociation:
    def test_returns_200(self, client):
        assert client.get(APPLE_PATH).status_code == 200

    def test_content_type_is_json(self, client):
        """Apple requires ``application/json`` on this extensionless file."""
        assert client.get(APPLE_PATH)["Content-Type"] == "application/json"

    def test_body_matches_committed_file(self, client):
        assert client.get(APPLE_PATH).content == _committed("apple-app-site-association.json")

    def test_body_declares_applinks(self, client):
        assert "applinks" in json.loads(client.get(APPLE_PATH).content)

    def test_unauthenticated(self, client):
        """Apple's CDN fetches anonymously — a 401/403 fails verification."""
        assert client.get(APPLE_PATH).status_code == 200


class TestAssetlinks:
    def test_returns_200(self, client):
        assert client.get(ANDROID_PATH).status_code == 200

    def test_content_type_is_json(self, client):
        assert client.get(ANDROID_PATH)["Content-Type"] == "application/json"

    def test_body_matches_committed_file(self, client):
        assert client.get(ANDROID_PATH).content == _committed("assetlinks.json")

    def test_body_declares_handle_all_urls_relation(self, client):
        statements = json.loads(client.get(ANDROID_PATH).content)
        assert statements[0]["relation"] == ["delegate_permission/common.handle_all_urls"]

    def test_unauthenticated(self, client):
        """Google fetches anonymously — a 401/403 fails verification."""
        assert client.get(ANDROID_PATH).status_code == 200


@pytest.mark.parametrize("path", [APPLE_PATH, ANDROID_PATH])
class TestNoRedirect:
    def test_exact_path_is_not_redirected(self, client, path):
        """Both platforms reject a redirect, so APPEND_SLASH must not fire here."""
        assert client.get(path).status_code == 200

    def test_trailing_slash_is_not_the_canonical_url(self, client, path):
        """The spec'd URL has no trailing slash; the slashed variant must 404."""
        assert client.get(f"{path}/").status_code == 404
