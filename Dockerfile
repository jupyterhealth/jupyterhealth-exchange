ARG PYTHON_VERSION=3.12-slim-bullseye

FROM python:${PYTHON_VERSION}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create directories
RUN mkdir -p /code
RUN mkdir -p /data

WORKDIR /code

# Install Python dependencies with caching
COPY Pipfile Pipfile.lock /code/
ARG XDG_CACHE_DIR=/tmp/cache
RUN --mount=type=cache,target=${XDG_CACHE_DIR} \
    export PIP_CACHE_DIR=$XDG_CACHE_DIR/pip \
 && export PIPENV_CACHE_DIR=$XDG_CACHE_DIR/pipenv \
 && pip install pipenv \
 && pipenv install --deploy --system \
 && pip uninstall -y pipenv

COPY . /code

# Collect Django static files
RUN python manage.py collectstatic --no-input

# Expose ports for Django and MCP server
EXPOSE 8000 8001

# Default command runs Django app
# For MCP server, override with: uvicorn mcp_server.src.jhe_mcp_http:app --host 0.0.0.0 --port 8001
CMD ["gunicorn", "--bind", ":8000", "--workers", "2", "jhe.wsgi"]
