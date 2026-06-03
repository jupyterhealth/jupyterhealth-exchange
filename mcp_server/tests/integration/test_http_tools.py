import os
import threading
import time

import httpx
import pytest
import uvicorn
from jhe_mcp.config import Settings
from jhe_mcp.server_http import build_app

_JHE_URL = os.environ.get("MCP_TEST_JHE_URL")
_TEST_TOKEN = os.environ.get("MCP_TEST_TOKEN")

_SKIP_REASON = "integration: set MCP_TEST_JHE_URL and MCP_TEST_TOKEN to run"


@pytest.fixture(scope="module")
def http_server(jhe_base_url):
    """Start the FastAPI app in-process on port 18401."""
    os.environ["JHE_BASE_URL"] = jhe_base_url
    os.environ.setdefault("JHE_CLIENT_ID", "test-client-id")
    settings = Settings.from_env()
    app = build_app(settings)

    config = uvicorn.Config(app, host="127.0.0.1", port=18401, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(30):
        try:
            r = httpx.get("http://127.0.0.1:18401/docs", timeout=1.0)
            if r.status_code in (200, 401, 404):
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        pytest.fail("HTTP server didn't start in 15 seconds")

    yield "http://127.0.0.1:18401"
    server.should_exit = True
    thread.join(timeout=5)


def test_unauthenticated_mcp_rejected(http_server):
    """Unauthenticated POST /mcp must return 401 with a WWW-Authenticate header."""
    r = httpx.post(f"{http_server}/mcp", timeout=5.0)
    assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text[:200]}"
    www_auth = r.headers.get("WWW-Authenticate", "")
    assert "resource_metadata" in www_auth, f"WWW-Authenticate header missing 'resource_metadata': {www_auth!r}"


def test_invalid_token_mcp_rejected(http_server):
    """POST /mcp with a bad bearer token must return 401."""
    r = httpx.post(
        f"{http_server}/mcp",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        timeout=5.0,
    )
    assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text[:200]}"
