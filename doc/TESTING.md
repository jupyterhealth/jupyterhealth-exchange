# Testing Guide — JupyterHealth Exchange

This document is the single source of truth for running, writing, and
understanding the JHE test suite.  It is written for **new contributors** who
have never seen this codebase before.

---

## Contents

| Section | What you'll learn |
|---------|-------------------|
| [Quick-start](#quick-start) | Run the full test suite in under a minute |
| [Test categories](#test-categories) | Unit vs. integration vs. smoke |
| [Smoke tests](#smoke-tests) | Post-deploy verification against a live URL |
| [Health endpoint](#health-endpoint) | The `/health` liveness probe |
| [Writing new tests](#writing-new-tests) | Conventions, fixtures, helpers |
| [CI pipelines](#ci-pipelines) | How tests run in GitHub Actions |
| [Troubleshooting](#troubleshooting) | Common issues and fixes |

---

## Quick-start

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | <https://www.python.org/downloads/> |
| Pipenv | latest | `pip install pipenv` |
| PostgreSQL | 16+ | <https://www.postgresql.org/download/> |

### 1. Install dependencies

```bash
cd jupyterhealth-exchange
pipenv install --dev
```

### 2. Set up the test database

The Django test runner creates a temporary database automatically.  You need a
running PostgreSQL instance and the following environment variables (or a
`.env` file):

```bash
DB_NAME=test_jhe_dev
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
SECRET_KEY=any-random-string
OIDC_RSA_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\nYOUR_KEY\n-----END RSA PRIVATE KEY-----"
```

> **Tip:** Copy `dot_env_example.txt` to `.env` and fill in values.

### 3. Run the full suite

```bash
pipenv run pytest tests/ -v
```

This runs **all unit and integration tests** (~180 tests).  Smoke tests are
automatically skipped unless you pass `--smoke-url` (see below).

### 4. Run a single test file

```bash
pipenv run pytest tests/test_smoke.py -v
```

### 5. Run only smoke tests against a live deployment

```bash
pipenv run pytest tests/test_smoke.py --smoke-url=https://jhe.fly.dev -m smoke -v
```

---

## Test categories

```
tests/
├── conftest.py                  # Shared fixtures + smoke-test infrastructure
├── utils.py                     # Helpers: create_study, add_observations, etc.
├── models.py                    # Test-only Django models
├── test_smoke.py                # 🔴 Smoke tests (live HTTP, requires --smoke-url)
├── test_model_methods.py        # Unit tests for model methods
├── test_get_setting_migration.py# Unit tests for JheSetting/get_setting
├── test_observation_viewset.py  # Integration: Observation API
├── test_observation_scope_filtering.py
├── test_data_source_viewset.py  # Integration: DataSource API
├── test_organization_viewset.py # Integration: Organization API
├── test_patient_viewset.py      # Integration: Patient API
├── test_practitioner_viewset.py # Integration: Practitioner API
├── test_study_viewset.py        # Integration: Study API
├── test_superuser_access.py     # Integration: superuser restrictions
├── test_user_profile.py         # Integration: user profile
└── ...
```

### Unit tests

- Operate on models and functions directly.
- Use the Django test database (created/destroyed automatically by pytest-django).
- Fast — no HTTP round-trips.

### Integration tests

- Issue HTTP requests via DRF's `APIClient` against the Django test server.
- Still use the test database — no external dependencies.
- Test the full request/response cycle including auth, serializers, and views.

### Smoke tests  *(new)*

- Make **real HTTP requests** to a live, deployed JHE instance.
- Require `--smoke-url` (auto-skipped otherwise).
- Marked with `@pytest.mark.smoke` so you can run them in isolation.
- Use the `requests` library with retries to handle Fly.io cold starts.
- **Do not** modify any data — read-only `GET` requests only.

---

## Smoke tests

### What they verify

| Priority | Endpoint | Assertion |
|----------|----------|-----------|
| **P0** | `GET /health` | 200, JSON with `status: ok` and `version` |
| **P0** | `GET /` | 200, contains "JupyterHealth" |
| **P0** | `GET /accounts/login/` | 200, contains `<form` |
| **P1** | `GET /api/schema/swagger-ui/` | 200, contains "swagger" |
| **P1** | `GET /api/schema/redoc/` | 200 |
| **P1** | `GET /portal/client_settings.js` | 200, contains `JHE_VERSION` |
| **P1** | Version consistency | `/health` version == `client_settings.js` version |
| **P2** | `GET /api/v1/*` (9 endpoints) | 401 or 403 without auth |
| **P2** | `GET /fhir/r5/*` (3 endpoints) | 401 or 403 without auth |
| **P2** | `GET /admin/` | 200 or 302 (redirect to login) |
| **P2** | `GET /static/admin/css/base.css` | 200, `text/css` |
| **P2** | HTTPS enforcement | HTTP → HTTPS redirect (if base is HTTPS) |
| **P2** | Security headers | `X-Content-Type-Options` present |
| **P2** | Debug leak check | `/health` contains no tracebacks |
| **P2** | 404 handling | Unknown path returns 404, not 500 |

### Running locally against the dev server

Start the Django dev server in one terminal:

```bash
pipenv run python manage.py runserver
```

Then in another terminal:

```bash
pipenv run pytest tests/test_smoke.py --smoke-url=http://localhost:8000 -m smoke -v
```

### Running against any JHE instance

The `--smoke-url` flag accepts **any** URL.  JHE is deployed to multiple
environments — just point the tests at whichever one you need:

```bash
# Fly.io dev instance
pipenv run pytest tests/test_smoke.py --smoke-url=https://jhe.fly.dev -m smoke -v

# UCSF production
pipenv run pytest tests/test_smoke.py --smoke-url=https://jhe.ucsf.edu -m smoke -v

# Local dev server
pipenv run pytest tests/test_smoke.py --smoke-url=http://localhost:8000 -m smoke -v
```

> **Note:** The first request may take 10-15 seconds if Fly machines are
> stopped.  The test suite has built-in retries (3 attempts, exponential
> backoff) to handle this.  Non-Fly instances respond immediately.

### How the `--smoke-url` opt-in works

1. `conftest.py` registers a `--smoke-url` CLI option via `pytest_addoption`.
2. `pytest_collection_modifyitems` auto-skips any test marked `@pytest.mark.smoke`
   when `--smoke-url` is not provided.
3. A session-scoped `smoke_url` fixture supplies the base URL to all smoke tests.
4. The `http` fixture in `test_smoke.py` creates a `requests.Session` with retry
   logic pre-configured.

This means:
- `pytest tests/` → smoke tests are **skipped** (normal dev workflow unaffected)
- `pytest tests/ --smoke-url=...` → smoke tests **run**
- `pytest tests/test_smoke.py -m smoke --smoke-url=...` → **only** smoke tests run

---

## Health endpoint

### Endpoint details

| Property | Value |
|----------|-------|
| **Path** | `/health` |
| **Method** | `GET` |
| **Auth** | None required |
| **DB query** | None — pure liveness check |
| **Response** | `{"status": "ok", "version": "v0.0.9"}` |
| **Content-Type** | `application/json` |
| **View** | `core.views.common.health` |

### Fly.io health check

The health endpoint is registered as a Fly HTTP health check in `fly.toml`:

```toml
[[http_service.checks]]
  grace_period = "30s"
  interval = "15s"
  method = "GET"
  path = "/health"
  timeout = "5s"
```

Fly monitors this endpoint and will restart machines that fail the check.

### Testing it manually

```bash
curl https://jhe.fly.dev/health
# {"status": "ok", "version": "v0.0.9"}
```

---

## Writing new tests

### Conventions

1. **File naming:** `tests/test_<feature>.py`
2. **Test classes:** Group related tests in classes prefixed with `Test`
3. **Docstrings:** Every test function should have a one-line docstring
   explaining what it verifies — these show up in `pytest -v` output
4. **Fixtures over setUp:** Use pytest fixtures, not `unittest.TestCase.setUp`
5. **Markers:**
   - `@pytest.mark.django_db` — tests that need database access
   - `@pytest.mark.smoke` — live deployment tests (require `--smoke-url`)

### Available fixtures (defined in `conftest.py`)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `organization` | function | An `Organization` named "Test Org" |
| `user` | function | A practitioner `JheUser` with a role in `organization` |
| `superuser` | function | A superuser `JheUser` |
| `patient` | function | A patient `JheUser` attached to `organization` |
| `device` | function | A `DataSource` named "test device" |
| `api_client` | function | DRF `APIClient` authenticated as `user` |
| `hr_study` | function | A `Study` with HeartRate scope in `organization` |
| `smoke_url` | session | Base URL from `--smoke-url` (smoke tests only) |

### Available helpers (defined in `utils.py`)

| Helper | Purpose |
|--------|---------|
| `Code` enum | `Code.HeartRate`, `Code.BloodPressure`, `Code.BloodGlucose` |
| `create_study(organization, codes)` | Create a study with scope codes |
| `add_patient_to_study(patient, study)` | Enroll a patient via consent |
| `add_observations(patient, device, codes, n)` | Create test observations |

### Example: adding a new unit test

```python
# tests/test_my_feature.py
import pytest

@pytest.mark.django_db
class TestMyFeature:
    def test_something_works(self, user, organization):
        """Descriptive one-liner about what this verifies."""
        # Arrange
        ...
        # Act
        result = my_function(user, organization)
        # Assert
        assert result == expected
```

### Example: adding a new smoke test

```python
# Add to tests/test_smoke.py (or a new file — mark with @pytest.mark.smoke)
import pytest

@pytest.mark.smoke
class TestMyNewEndpoint:
    def test_my_endpoint_responds(self, http):
        """GET /my-endpoint/ → 200."""
        resp = _get(http, "/my-endpoint/")
        assert resp.status_code == 200
```

---

## CI pipelines

### Overview

| Workflow | File | Trigger | What it does |
|----------|------|---------|--------------|
| Backend tests | `.github/workflows/be_test.yml` | Push/PR to `main` | Full pytest suite with Postgres |
| Frontend tests | `.github/workflows/fe_test.yml` | Push/PR to `main` | Jest tests for JS |
| Deploy | `.github/workflows/fly_dev.yml` | Push to `main` | Fly.io deploy + smoke tests (jhe.fly.dev) |
| Smoke (on-demand) | `.github/workflows/smoke_test.yml` | Manual dispatch | Smoke tests against **any** URL |
| Container image | `.github/workflows/image.yaml` | Push/PR to `main` | Build + push to GHCR |

### Deploy + smoke workflow (`fly_dev.yml`)

```
push to main
    │
    ├── Job: deploy
    │     └── flyctl deploy -a jhe
    │
    └── Job: smoke-test (runs after deploy succeeds)
          ├── pip install pytest requests
          ├── Wait 30s for machines to stabilize
          └── pytest tests/test_smoke.py --smoke-url=https://jhe.fly.dev -m smoke
```

The smoke tests run as a **separate job** that depends on the deploy job.
If smoke tests fail, the workflow fails — giving you a red check on the commit.

### On-demand smoke workflow (`smoke_test.yml`)

Use this to run smoke tests against **any** JHE instance — UCSF, a staging
environment, etc.:

1. Go to **Actions → Smoke Tests (on-demand)** in GitHub
2. Click **Run workflow**
3. Enter the base URL (e.g., `https://jhe.ucsf.edu`)
4. Click **Run workflow** again to start

```
workflow_dispatch (manual trigger)
    │
    └── Job: smoke-test
          ├── pip install pytest requests
          ├── Warm-up ping to /health (handles cold starts)
          └── pytest tests/test_smoke.py --smoke-url=<user-provided URL> -m smoke
```

This keeps CI simple: the auto-deploy workflow only tests `jhe.fly.dev`,
and you manually trigger tests for other instances only when needed.

> **Important:** Both smoke CI jobs use `--override-ini="DJANGO_SETTINGS_MODULE="`
> to clear the Django settings module.  Smoke tests don't import Django — they
> only use `requests` to make HTTP calls.  This prevents import errors when
> Django dependencies (Postgres, etc.) aren't available in the CI runner.

### Backend test workflow (`be_test.yml`)

- Spins up a PostgreSQL 16 service container
- Installs all dev dependencies via Pipenv
- Runs `pipenv run pytest -vx tests/`
- Smoke tests are auto-skipped (no `--smoke-url` provided)
- Uploads coverage to Codecov

---

## Troubleshooting

### "need --smoke-url to run smoke tests"

This is expected — smoke tests are opt-in.  Add the flag:

```bash
pytest tests/test_smoke.py --smoke-url=http://localhost:8000 -m smoke -v
```

### Smoke tests time out / fail with ConnectionError

Fly.io machines may be stopped (auto-stop is enabled).  The first request
triggers a cold start that can take 10-15 seconds.  The test suite retries
up to 3 times with exponential backoff, but if your network is slow or the
machine takes longer, increase `REQUEST_TIMEOUT` in `test_smoke.py`.

### Smoke tests pass locally but fail in CI

Check:
1. **Was the deploy successful?**  The smoke job depends on deploy — if deploy
   failed, smoke won't run.
2. **Is the Fly app reachable?**  Check `https://jhe.fly.dev/health` manually.
3. **DNS/network issues?**  GitHub Actions runners are in Azure — ensure Fly
   isn't blocking those IPs.

### `test_health_*` unit tests fail with "No such table"

These unit tests need `@pytest.mark.django_db`.  This is already set on the
`TestHealthViewUnit` class, but if you move tests, make sure to include the
marker.

### Pre-existing test failures

The following tests have a **known, pre-existing failure** related to base64
encoding in observation uploads.  These are **not** caused by the smoke test
changes:

- `test_fhir_create`
- `test_observation_upload_bundle`
- `test_observation_upload`

### Import errors when running smoke tests standalone

If you see Django import errors when running smoke tests in a minimal
environment (e.g., CI without Postgres), use these overrides:

```bash
pytest tests/test_smoke.py \
  --smoke-url=https://jhe.fly.dev \
  -m smoke \
  --override-ini="addopts=" \
  --override-ini="DJANGO_SETTINGS_MODULE="
```

This clears the Django settings module and `addopts` (which includes
`--cov` flags that would try to import Django modules).
