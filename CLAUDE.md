# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

JupyterHealth Exchange is a Django 5.2 web application that facilitates sharing of user-consented medical data through:
- Web UI with Vanilla JS SPA (no npm/React required for runtime)
- REST APIs using Django Rest Framework
- FHIR R5 compliant APIs with schema validation
- OAuth 2.0, OIDC, and SMART on FHIR identity provision

## Key Commands

### Development Setup
```bash
# Install Python dependencies (requires Python 3.10-3.13)
pipenv sync  # or pipenv install if starting fresh

# Database setup (Postgres required)
python manage.py migrate
python manage.py seed  # Seeds initial data

# Run development server
python manage.py runserver
```

### Testing
```bash
# Run Python tests
python manage.py test

# Run frontend JavaScript tests
cd jhe/core/static
npm test
npm run test:watch  # Watch mode
```

### Code Quality
```bash
# Pre-commit hooks (run before each commit)
pre-commit run --all-files

# Install git hooks for automatic checks
pre-commit install

# Individual tools
black --line-length=120 .  # Format Python code
flake8 --max-line-length=120  # Lint Python code
```

### Production
```bash
# Collect static files
python manage.py collectstatic --no-input

# Run with gunicorn
gunicorn --bind :8000 --workers 2 jhe.wsgi
```

### Database Management
```bash
# Create new migration
python manage.py makemigrations

# Seed with additional test data
python manage.py iglu  # Loads iglu project test data (takes 10-20 min)
```

## Architecture Overview

### Django Structure
- **jhe/** - Main Django project directory
  - **jhe/** - Settings, URLs, WSGI/ASGI configuration
  - **core/** - Main application module containing:
    - **models.py** - FHIR-compliant data models (Patient, Organization, Study, Observation)
    - **views/** - API views organized by resource type
    - **serializers.py** - DRF serializers with Pydantic validation for FHIR
    - **static/** - Frontend SPA (Vanilla JS + Handlebars templates)
    - **templates/** - Django templates for initial page load
    - **management/commands/** - Custom Django commands (seed, iglu)

### Key Data Models & Relationships
- **Organization** → hierarchical structure with sub-organizations
- **Study** (FHIR Group) → belongs to single Organization
- **Patient** → belongs to Organizations, consents to Studies
- **Practitioner** (User) → belongs to Organizations with roles (Manager/Member/Viewer)
- **Observation** → patient data with Open mHealth JSON format support
- **DataSource** → devices/apps that produce Observations

### Frontend Architecture
- Single Page Application without build tools
- Located in `jhe/core/static/`
- Uses oidc-client-ts for authentication
- Handlebars for client-side templating
- Bootstrap for styling

### API Structure
- **Admin REST API** (`/api/v1/`) - Organization/Study/Patient management
- **FHIR API** (`/fhir/r5/`) - FHIR R5 compliant endpoints
- **Auth API** (`/o/`) - OAuth 2.0/OIDC endpoints

### Database
- PostgreSQL required (uses JSON functions)
- Models use Django ORM with JSONB fields for FHIR data
- Direct SQL queries with JSON responses for performance

## Important Conventions

### FHIR Compliance
- Uses camelCase for API responses (via djangorestframework-camel-case)
- Validates against FHIR R5 schemas using fhir.resources
- Stores Open mHealth data as Base64 encoded JSON in valueAttachment

### Authentication
- OAuth 2.0 Authorization Code flow with PKCE
- Practitioners use standard web login
- Patients receive invitation links with pre-generated auth codes
- Static PKCE values for Patient auth (configured in .env)

### Code Style
- Python: Black formatter with 120 char lines
- Excludes migrations and data directories from formatting
- Pre-commit hooks enforce standards

### Environment Configuration
- Uses .env file (see dot_env_example.txt)
- Key variables: DB_*, OIDC_*, PATIENT_AUTHORIZATION_CODE_*
- SAML2 SSO optional (SAML2_ENABLED, SSO_VALID_DOMAINS)

## Testing Approach
- Django TestCase for backend tests
- Jest for frontend JavaScript tests
- Test files located in `jhe/tests/` and `jhe/core/static/tests/`
- Coverage tracking with coverage.py

## Security Considerations
- HTTPS required for non-localhost deployments
- Never commit secrets to repository
- Use environment variables for sensitive configuration
- Django DEBUG must be False in production