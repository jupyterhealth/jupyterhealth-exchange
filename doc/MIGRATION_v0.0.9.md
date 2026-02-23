# Migration Guide: v0.0.8 → v0.0.9

This document covers the steps required to upgrade JupyterHealth Exchange from **v0.0.8** to **v0.0.9**.

---

## Overview of Changes

v0.0.9 includes ~60 commits with the following highlights:

| Category | Change | PR |
|----------|--------|----|
| **Settings** | ENV config migrated to DB-backed `JheSetting` model | [#298](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/298) |
| **Multi-client** | New `ClientDataSource` and `StudyClient` models; Clients UI | [#290](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/290) |
| **Security** | System settings & practitioners endpoints restricted to superusers | [#302](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/302) |
| **Privacy** | PHI (name, email) removed from patient `/profile` response | [#301](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/301) |
| **Frontend** | Site URL replaced with `window.origin` (no more `SITE_URL` env var needed by the frontend) | [#299](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/299) |
| **Seed data** | Real institution names replaced with synthetic/fictitious names | [#193](https://github.com/jupyterhealth/jupyterhealth-exchange/issues/193) |
| **Observation scope** | Fixed observation scope filtering when querying by study | [#228](https://github.com/jupyterhealth/jupyterhealth-exchange/issues/228) |
| **FHIR** | Fixed pagination for FHIR endpoints | [#276](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/276) |
| **Admin** | Default Django pagination for admin API | [#289](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/289) |
| **Infra** | Debian image upgraded from 11 to 13; `ALLOWED_HOSTS` configurable via env; `DEBUG` no longer force-set to `True` | [#249](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/249), [#250](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/250), [#280](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/280) |
| **SMART** | Incomplete SMART launch handlers disabled | [#283](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/283) |

---

## 1. Database Migrations

Five new migrations ship with v0.0.9:

| Migration | What it does |
|-----------|-------------|
| `0012_clientdatasource` | Creates `ClientDataSource` (links OAuth Application ↔ DataSource) |
| `0013_studyclient_and_more` | Creates `StudyClient` (links OAuth Application ↔ Study) |
| `0014_…unique_study_id_client_id` | Adds unique constraint on `StudyClient(study, client)` |
| `0015_…jhesetting` | Creates `JheSetting` model; changes `Patient.last_updated` and `Practitioner.last_updated` to `auto_now=True` |
| `0016_…remove_practitioner_birth_date` | **Removes `Practitioner.birth_date` column**; updates `DataSource.type` choices |

### Fly.io deployments

Migrations run automatically on deploy via `release_command` in `fly.toml`:

```toml
[deploy]
  release_command = "python manage.py migrate --noinput"
```

No manual action is required — pushing to `main` triggers the GitHub Actions workflow (`.github/workflows/fly_dev.yml`) which deploys and migrates automatically.

### Manual / self-hosted deployments

Run:

```bash
python manage.py migrate
```

> **Breaking schema change:** Migration `0016` **drops the `Practitioner.birth_date` column**. If you have downstream code or queries that reference this field, update them before migrating.

---

## 2. Populate JheSetting Records (Critical)

v0.0.9 moves several configuration values from environment variables into the database via the new `JheSetting` model. The application falls back to env vars or defaults when a `JheSetting` record is missing, but populating them is strongly recommended.

### Option A: Re-run the seed command (easiest for dev/staging)

```bash
python manage.py seed
```

This creates all required `JheSetting` records with sensible defaults (random invite code, random PKCE verifier, auto-generated RSA key, etc.). **Warning:** this also recreates seed organizations, studies, and users — use only on dev/staging environments where that is acceptable.

### Option B: Populate via Django Admin (production)

Browse to `/admin` → **Core → Jhe settings** and create the following records:

| Key | Value Type | Value | Notes |
|-----|-----------|-------|-------|
| `site.url` | string | `https://your-domain.fly.dev` | Full URL, no trailing slash |
| `site.ui.title` | string | `JupyterHealth Exchange` | Displayed in the UI header |
| `site.time_zone` | string | `America/Los_Angeles` | Used for date display |
| `site.registration_invite_code` | string | *(your invite code)* | Code given to new users to register |
| `site.secret_key` | string | *(random 50+ chars)* | Used for CSRF/session signing; generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `auth.default_orgs` | string | e.g. `20001:viewer;20002:manager` | Semicolon-separated `org_id:role` pairs assigned to new users on registration |
| `auth.private_key` | string | *(RSA PEM)* | OIDC signing key; generate per [django-oauth-toolkit docs](https://django-oauth-toolkit.readthedocs.io/en/latest/oidc.html#creating-rsa-private-key) |
| `auth.sso.saml2` | int | `0` | Set to `1` to enable SAML SSO |
| `auth.sso.idp_metadata_url` | string | `""` | SAML IdP metadata URL (if SSO enabled) |
| `auth.sso.valid_domains` | string | `""` | Comma-separated email domains for SSO |

### Option C: Populate via Django shell

```python
from core.models import JheSetting

settings_to_create = [
    ("site.url", "string", "https://your-domain.fly.dev"),
    ("site.registration_invite_code", "string", "your-invite-code"),
    ("site.secret_key", "string", "generate-a-random-value"),
    ("auth.private_key", "string", "-----BEGIN RSA PRIVATE KEY-----\n..."),
    ("auth.default_orgs", "string", ""),
]

for key, vtype, value in settings_to_create:
    obj, created = JheSetting.objects.get_or_create(
        key=key, setting_id=None,
        defaults={"value_type": vtype}
    )
    obj.set_value(vtype, value)
    obj.save()
    print(f"{'Created' if created else 'Updated'}: {key}")
```

### Per-Client Settings

Each OAuth2 client application can have its own settings. These are `JheSetting` records where `setting_id` equals the client's `Application.pk`:

| Key | Value Type | Value | Notes |
|-----|-----------|-------|-------|
| `client.code_verifier` | string | *(base64-encoded random)* | PKCE code verifier for this client |
| `client.invitation_url` | string | URL with `CODE` placeholder | Patient invitation deep-link; `CODE` is replaced at runtime |

These can be managed from the **Clients** section in the admin UI (superuser only).

---

## 3. Multi-Client Setup

v0.0.9 introduces multi-client support. Each OAuth2 Application can now be associated with specific data sources and studies:

1. **Browse to** `/admin` → **Clients** (or use the new Clients UI at `/portal/clients`)
2. **Associate data sources** with each client via `ClientDataSource`
3. **Associate studies** with each client from the Study detail page

If you have only a single client (the default seeded one), no action is required — existing behavior is preserved.

---

## 4. Authorization Code Coordination

If you are integrating with CommonHealth or another patient-facing app, the PKCE `code_verifier` is now stored per-client in `JheSetting` instead of as a global constant in `settings.py`. Coordinate with Simona / the mobile app team to ensure:

- The `client.code_verifier` value in `JheSetting` matches what the mobile app uses
- The `client.invitation_url` template is correct for your app store listing

---

## 5. Environment Variable Changes

### Removed from `settings.py`

| Variable | Status | Notes |
|----------|--------|-------|
| `PATIENT_AUTHORIZATION_CODE_CHALLENGE` | **Removed** | Was a hardcoded constant; challenge is now computed dynamically from the per-client `code_verifier` stored in `JheSetting` |
| `PATIENT_AUTHORIZATION_CODE_VERIFIER` | **Removed** | Now stored per-client in `JheSetting` (key: `client.code_verifier`) |

### Still used (no change)

| Variable | Notes |
|----------|-------|
| `OIDC_RSA_PRIVATE_KEY` | Still read from env as fallback; prefer migrating to `JheSetting` `auth.private_key` |
| `SECRET_KEY` | Still read from env as fallback; prefer migrating to `JheSetting` `site.secret_key` |
| `DB_*` | Database connection — unchanged |
| `SMTP_*` | Email config — unchanged |
| `ALLOWED_HOSTS` | **New in v0.0.9** — comma-separated hostnames (defaults to `["*"]`) |
| `X_FRAME_OPTIONS` | Unchanged |
| `PATIENT_AUTHORIZATION_CODE_EXPIRE_SECONDS` | Unchanged (still in `settings.py`) |

---

## 6. Breaking Changes

### API

- **`GET /api/patient/profile`** no longer returns `first_name`, `last_name`, or `email`. Downstream clients that display patient identity info from this endpoint will need to source it elsewhere. ([#301](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/301))

- **`GET /api/settings`** and **`GET /api/practitioners`** are now restricted to **superusers only**. Non-superuser requests will receive `403 Forbidden`. ([#302](https://github.com/jupyterhealth/jupyterhealth-exchange/pull/302))

### Schema

- **`Practitioner.birth_date`** column is dropped by migration `0016`. Any custom queries or reports using this field must be updated.

### Seed Data

- The `seed` command now uses **synthetic/fictitious institution names** (e.g. "Example University", "Example Medical University") instead of real ones (UC Berkeley, UCSF). This affects local development and demo environments only.

---

## 7. Post-Upgrade Verification

After deploying, verify the upgrade was successful:

1. **Check version:** `GET /api/settings` (as superuser) should show `version: v0.0.9`
2. **Check migrations:** `python manage.py showmigrations core` — all should be `[X]`
3. **Check settings:** Browse to `/admin` → **Jhe settings** — verify critical keys exist
4. **Test auth flow:** Log in via the UI at `/` — OIDC flow should complete normally
5. **Test patient profile:** `GET /api/patient/profile` — confirm PHI fields are absent

---

## 8. Rollback

If you need to roll back:

1. **Database:** Reverse migrations with `python manage.py migrate core 0011` — this will drop the `JheSetting`, `StudyClient`, and `ClientDataSource` tables and restore `Practitioner.birth_date`
2. **Code:** Check out the `v0.0.8` tag: `git checkout v0.0.8`
3. **Constants:** The removed `PATIENT_AUTHORIZATION_CODE_CHALLENGE` and `PATIENT_AUTHORIZATION_CODE_VERIFIER` will be restored in `settings.py` at the v0.0.8 tag

> **Warning:** Rolling back migrations will **permanently delete** any data in the `JheSetting`, `StudyClient`, and `ClientDataSource` tables. Export this data first if needed.
