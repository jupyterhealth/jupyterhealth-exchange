# syntax=docker/dockerfile:1

# ---- Builder stage ---------------------------------------------------------
# Reproducible install from the committed uv.lock (no version-range resolution).
FROM python:3.12-slim-bookworm@sha256:93ab4b7fa528b25124c97bcc755415e60eb671a86b4dbe0328df2fe2d1c1193d AS builder

# Pinned uv binary for deterministic, lockfile-driven installs.
COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Layer 1: install only third-party dependencies (cached unless lock changes).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Layer 2: install the project itself once sources are present.
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- Runtime stage ---------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:93ab4b7fa528b25124c97bcc755415e60eb671a86b4dbe0328df2fe2d1c1193d AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root user (fixed high uid) owns the application directory.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Copy the fully built application (including the .venv) from the builder.
COPY --from=builder --chown=10001:10001 /app /app

# Remove the system package manager from the runtime image to shrink the RCE
# blast radius. The app runs from the prebuilt /app/.venv on PATH and needs no
# pip at runtime. Use the absolute system interpreter (not the venv python that
# leads on PATH) so the base-image pip/setuptools/wheel are actually removed.
# Done while still root, before dropping to the non-root user.
RUN /usr/local/bin/python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true

USER 10001

EXPOSE 8401

CMD ["jhe-mcp-http"]
