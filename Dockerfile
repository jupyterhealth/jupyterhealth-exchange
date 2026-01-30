ARG PYTHON_VERSION=3.12-slim-trixie

FROM python:${PYTHON_VERSION}

RUN apt-get -y update \
 && apt-get -y install --no-install-recommends postgresql-client \
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
 && pipenv install --deploy --system \
 && pip uninstall -y pipenv

COPY . /code
RUN python manage.py collectstatic --no-input

EXPOSE 8000

CMD ["gunicorn", "--bind", ":8000", "--workers", "2", "jhe.wsgi"]
