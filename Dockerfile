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

COPY Pipfile Pipfile.lock /code/
ARG XDG_CACHE_DIR=/tmp/cache
RUN --mount=type=cache,target=${XDG_CACHE_DIR} \
    export PIP_CACHE_DIR=$XDG_CACHE_DIR/pip \
 && export PIPENV_CACHE_DIR=$XDG_CACHE_DIR/pipenv \
 && pip install pipenv \
 && pipenv install --system \
 && pip uninstall -y pipenv

# supercronic for the optional jhe_cron sidecar (runs ow_poll on a schedule).
ADD https://github.com/aptible/supercronic/releases/download/v0.2.29/supercronic-linux-amd64 /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

COPY . /code
RUN python manage.py collectstatic --no-input

EXPOSE 8000

CMD ["gunicorn", "--bind", ":8000", "--workers", "2", "jhe.wsgi"]
