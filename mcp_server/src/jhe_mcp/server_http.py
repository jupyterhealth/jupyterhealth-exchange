from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from jhe_mcp.auth.broker import build_broker_router
from jhe_mcp.config import Settings
from jhe_mcp.core import build_server
from jhe_mcp.fhir.client import assert_request_ctx_importable

logger = logging.getLogger(__name__)


def build_app(settings: Settings) -> FastAPI:
    # Fail fast at boot if the per-request token context import has moved (e.g.
    # after an mcp upgrade); otherwise per-request auth would silently regress.
    assert_request_ctx_importable()
    mcp = build_server(settings)

    # In resource-server mode the MCP SDK wraps /mcp with RequireAuthMiddleware,
    # which verifies the bearer token on *every* request via JheTokenVerifier and
    # binds the per-request principal (see core.build_server). We therefore do NOT
    # gate /mcp in a FastAPI middleware here — doing so would double-auth and, worse,
    # snapshot the initialize-time token into a contextvar (the original isolation
    # bug). The broker routes and /health were never gated and remain open.
    streamable_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="jhe-mcp HTTP", lifespan=lifespan)
    app.include_router(build_broker_router(settings))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.mount("/", streamable_app)
    return app


def configure_logging() -> None:
    """Log hygiene: keep PHI-adjacent identifiers out of general logs.

    httpx emits an INFO line per request that includes the full request URL
    (which embeds study/patient ids); raising it to WARNING suppresses those.
    Uvicorn's access log echoes request lines and is disabled at ``main()``.
    The deliberate ``jhe_mcp.audit`` log (and other ``jhe_mcp.*`` loggers) is
    the controlled record and is intentionally left untouched.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    import uvicorn

    configure_logging()
    settings = Settings.from_env()
    app = build_app(settings)
    port = int(os.environ.get("MCP_HTTP_PORT", "8401"))
    uvicorn.run(app, host="0.0.0.0", port=port, access_log=False)


if __name__ == "__main__":
    main()
