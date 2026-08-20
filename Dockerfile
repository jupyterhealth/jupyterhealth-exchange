ARG PYTHON_VERSION=3.11-slim-trixie

FROM python:${PYTHON_VERSION}

RUN apt-get -y update \
 && apt-get -y install --no-install-recommends postgresql-client git \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /code
RUN mkdir -p /data

WORKDIR /code

COPY pyproject.toml uv.lock /code/
ARG XDG_CACHE_DIR=/tmp/cache
# Export the locked graph (production deps only) and install into the system
# interpreter, preserving the same site-packages layout pipenv --system used.
RUN --mount=type=cache,target=${XDG_CACHE_DIR} \
    --mount=type=bind,from=ghcr.io/astral-sh/uv:0.11.7,source=/uv,target=/usr/local/bin/uv \
    export UV_CACHE_DIR=$XDG_CACHE_DIR/uv \
 && uv export --frozen --no-dev --no-emit-project --format requirements.txt -o /tmp/requirements.txt \
 && uv pip install --system --no-deps -r /tmp/requirements.txt

# supercronic for the optional jhe_cron sidecar (runs ow_poll on a schedule).
# TARGETARCH is automatically set by Docker BuildKit / buildx to match the
# image's target platform (amd64, arm64, arm). Supercronic publishes a
# matching binary for each. `--chmod=755` sets the executable bit in the
# same layer as the download, avoiding a duplicate-binary layer that a
# separate `RUN chmod +x` would otherwise create.
ARG TARGETARCH
ADD --chmod=755 https://github.com/aptible/supercronic/releases/download/v0.2.29/supercronic-linux-${TARGETARCH} /usr/local/bin/supercronic

COPY . /code
RUN python manage.py collectstatic --no-input

EXPOSE 8000

CMD ["gunicorn", "--bind", ":8000", "--workers", "2", "jhe.wsgi"]
