ARG PYTHON_VERSION=3.12-slim-bullseye

FROM python:${PYTHON_VERSION}

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies for both Django and MCP server
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create directories
RUN mkdir -p /code
RUN mkdir -p /data

WORKDIR /code

# Install Python dependencies
RUN pip install pipenv
COPY Pipfile Pipfile.lock /code/
RUN pipenv install --deploy --system

# Copy application code
COPY . /code

# Collect Django static files
RUN python manage.py collectstatic --no-input

# Expose ports for Django and MCP server
EXPOSE 8000 8001

# Default command runs Django app
# For MCP server, override with: uvicorn mcp_server.src.jhe_mcp_http:app --host 0.0.0.0 --port 8001
CMD ["gunicorn", "--bind", ":8000", "--workers", "2", "jhe.wsgi"]
