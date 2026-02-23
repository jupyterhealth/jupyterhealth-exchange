"""
Smoke tests for a live JupyterHealth Exchange deployment.

These tests make real HTTP requests to a running JHE instance and verify that
critical endpoints respond correctly.  They are *not* run during the normal
``pytest tests/`` invocation — you must supply a URL explicitly::

    pytest tests/test_smoke.py --smoke-url=https://jhe.fly.dev -m smoke -v

See ``doc/TESTING.md`` for full instructions.
"""

import re

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Generous timeouts — Fly machines may cold-start on the first request.
REQUEST_TIMEOUT = 30  # seconds

#: Retry strategy for transient failures (502/503 during cold start).
_RETRY = Retry(
    total=3,
    backoff_factor=2,
    status_forcelist=[502, 503, 504],
    allowed_methods=["GET"],
)


@pytest.fixture(scope="session")
def http(smoke_url):
    """A ``requests.Session`` pre-configured with retries and base URL."""
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    # Store base so individual tests just append a path.
    session._smoke_base = smoke_url  # noqa: SLF001 — private attr on purpose
    return session


def _get(http, path, **kwargs):
    """GET ``<base><path>`` with default timeout and return the response."""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    # Don't follow redirects automatically — some tests assert on 302.
    kwargs.setdefault("allow_redirects", False)
    return http.get(f"{http._smoke_base}{path}", **kwargs)  # noqa: SLF001


# ===================================================================
# P0 — Core liveness
# ===================================================================


@pytest.mark.smoke
class TestP0Liveness:
    """Absolute minimum: is the app alive and rendering?"""

    def test_health_endpoint_returns_ok(self, http):
        """``GET /health`` → 200, JSON with status & version."""
        resp = _get(http, "/health")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        # Version should look like v<major>.<minor>.<patch>
        assert re.match(r"^v\d+\.\d+\.\d+", data["version"]), f"Unexpected version format: {data['version']}"

    def test_health_content_type_is_json(self, http):
        """``GET /health`` responds with ``application/json``."""
        resp = _get(http, "/health")
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("Content-Type", "")

    def test_homepage_renders(self, http):
        """``GET /`` → 200, contains 'JupyterHealth'."""
        resp = _get(http, "/", allow_redirects=True)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        body = resp.text.lower()
        assert "jupyterhealth" in body, "Homepage should mention JupyterHealth"

    def test_login_page_renders(self, http):
        """``GET /accounts/login/`` → 200, contains a form."""
        resp = _get(http, "/accounts/login/", allow_redirects=True)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert "<form" in resp.text.lower(), "Login page should contain an HTML form"


# ===================================================================
# P1 — API documentation
# ===================================================================


@pytest.mark.smoke
class TestP1APIDocs:
    """API schema / documentation endpoints should be publicly accessible."""

    def test_swagger_ui_loads(self, http):
        """``GET /api/schema/swagger-ui/`` → 200."""
        resp = _get(http, "/api/schema/swagger-ui/", allow_redirects=True)
        assert resp.status_code == 200, f"Swagger UI: expected 200, got {resp.status_code}"
        assert "swagger" in resp.text.lower(), "Response should contain Swagger content"

    def test_redoc_loads(self, http):
        """``GET /api/schema/redoc/`` → 200."""
        resp = _get(http, "/api/schema/redoc/", allow_redirects=True)
        assert resp.status_code == 200, f"ReDoc: expected 200, got {resp.status_code}"


# ===================================================================
# P1 — Version verification
# ===================================================================


@pytest.mark.smoke
class TestP1Version:
    """Verify the deployed version matches expectations."""

    def test_client_settings_js_contains_version(self, http):
        """``GET /portal/client_settings.js`` → 200, includes ``JHE_VERSION``."""
        resp = _get(http, "/portal/client_settings.js", allow_redirects=True)
        assert resp.status_code == 200, f"client_settings.js: expected 200, got {resp.status_code}"
        assert "JHE_VERSION" in resp.text, "client_settings.js should contain JHE_VERSION"

    def test_health_version_matches_client_settings(self, http):
        """The version reported by ``/health`` must match ``/portal/client_settings.js``."""
        health_resp = _get(http, "/health")
        health_version = health_resp.json()["version"]

        js_resp = _get(http, "/portal/client_settings.js", allow_redirects=True)
        # Extract version from JS like: JHE_VERSION: "v0.0.9"
        match = re.search(r'JHE_VERSION["\s:]+["\']?(v[\d.]+)', js_resp.text)
        assert match, f"Could not extract JHE_VERSION from client_settings.js:\n{js_resp.text[:300]}"
        js_version = match.group(1)

        assert health_version == js_version, (
            f"/health reports {health_version} but client_settings.js reports {js_version}"
        )


# ===================================================================
# P2 — Authentication enforcement
# ===================================================================


@pytest.mark.smoke
class TestP2AuthEnforcement:
    """Authenticated endpoints must reject unauthenticated requests."""

    @pytest.mark.parametrize(
        "path, description",
        [
            ("/api/v1/", "Admin API root"),
            ("/api/v1/users", "Users list"),
            ("/api/v1/practitioners", "Practitioners list"),
            ("/api/v1/organizations", "Organizations list"),
            ("/api/v1/patients", "Patients list"),
            ("/api/v1/studies", "Studies list"),
            ("/api/v1/observations", "Observations list"),
            ("/api/v1/data_sources", "Data sources list"),
            ("/api/v1/jhe_settings", "JHE settings list"),
        ],
        ids=lambda v: v if isinstance(v, str) and v.startswith("/") else "",
    )
    def test_admin_api_requires_auth(self, http, path, description):
        """``GET {path}`` without credentials → 401 or 403."""
        resp = _get(http, path, allow_redirects=False)
        assert resp.status_code in (401, 403), (
            f"{description} ({path}): expected 401/403, got {resp.status_code}"
        )

    @pytest.mark.parametrize(
        "path, description",
        [
            ("/fhir/r5/", "FHIR base"),
            ("/fhir/r5/Observation", "FHIR Observation"),
            ("/fhir/r5/Patient", "FHIR Patient"),
        ],
        ids=lambda v: v if isinstance(v, str) and v.startswith("/") else "",
    )
    def test_fhir_api_requires_auth(self, http, path, description):
        """``GET {path}`` without credentials → 401 or 403."""
        resp = _get(http, path, allow_redirects=False)
        assert resp.status_code in (401, 403), (
            f"{description} ({path}): expected 401/403, got {resp.status_code}"
        )


# ===================================================================
# P2 — Admin site
# ===================================================================


@pytest.mark.smoke
class TestP2AdminSite:
    """Django admin must be reachable (redirects to login if unauthenticated)."""

    def test_admin_accessible(self, http):
        """``GET /admin/`` → 200 (login page) or 302 (redirect to login)."""
        resp = _get(http, "/admin/", allow_redirects=False)
        assert resp.status_code in (200, 301, 302), (
            f"Admin: expected 200/301/302, got {resp.status_code}"
        )

    def test_admin_login_page_renders(self, http):
        """Following redirects from ``/admin/`` should reach an HTML page."""
        resp = _get(http, "/admin/", allow_redirects=True)
        assert resp.status_code == 200, f"Admin login: expected 200, got {resp.status_code}"
        assert "<!doctype html>" in resp.text.lower() or "<html" in resp.text.lower(), (
            "Admin should return HTML"
        )


# ===================================================================
# P2 — Static files & HTTPS
# ===================================================================


@pytest.mark.smoke
class TestP2Infrastructure:
    """Infrastructure-level checks: static files, HTTPS, headers."""

    def test_static_files_served(self, http):
        """A known static asset should be reachable (e.g. admin CSS)."""
        # Django admin CSS is always present when collectstatic has run.
        resp = _get(http, "/static/admin/css/base.css", allow_redirects=True)
        assert resp.status_code == 200, (
            f"Static file: expected 200, got {resp.status_code}"
        )
        assert "text/css" in resp.headers.get("Content-Type", ""), (
            "Static CSS file should have text/css content type"
        )

    def test_https_enforcement(self, http):
        """If the base URL is HTTPS, verify the server enforces it."""
        if not http._smoke_base.startswith("https://"):  # noqa: SLF001
            pytest.skip("Base URL is not HTTPS — skipping HTTPS enforcement test")
        # Try the HTTP equivalent — should redirect to HTTPS.
        http_url = http._smoke_base.replace("https://", "http://", 1)  # noqa: SLF001
        try:
            resp = requests.get(
                f"{http_url}/health",
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
            # Should be a 301/308 redirect to HTTPS, or connection refused.
            assert resp.status_code in (301, 302, 307, 308), (
                f"HTTP→HTTPS redirect: expected 3xx, got {resp.status_code}"
            )
        except requests.ConnectionError:
            # Connection refused on port 80 is acceptable — HTTPS-only.
            pass


# ===================================================================
# P2 — Response quality
# ===================================================================


@pytest.mark.smoke
class TestP2ResponseQuality:
    """Verify response headers and content quality."""

    def test_homepage_has_security_headers(self, http):
        """Homepage should include basic security headers."""
        resp = _get(http, "/", allow_redirects=True)
        # X-Content-Type-Options is set by Django's SecurityMiddleware
        assert "x-content-type-options" in {
            k.lower() for k in resp.headers
        }, "Missing X-Content-Type-Options header"

    def test_health_does_not_leak_debug_info(self, http):
        """``/health`` response must not contain stack traces or DEBUG artifacts."""
        resp = _get(http, "/health")
        body = resp.text.lower()
        assert "traceback" not in body, "/health should not contain tracebacks"
        assert "debug" not in body, "/health should not contain debug info"

    def test_404_returns_proper_status(self, http):
        """A nonsense path should return 404, not 500."""
        resp = _get(http, "/this-path-should-not-exist-abc123/", allow_redirects=True)
        assert resp.status_code == 404, (
            f"Unknown path: expected 404, got {resp.status_code}"
        )


# ===================================================================
# Unit test for /health (runs in normal pytest with Django test client)
# ===================================================================


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
