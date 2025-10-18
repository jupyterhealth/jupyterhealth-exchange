========================
Security and Compliance
========================

JupyterHealth Exchange implements a comprehensive security architecture designed for healthcare environments, ensuring HIPAA compliance, data privacy, and robust access controls.

.. contents:: Security Topics
   :local:
   :depth: 2

Multi-Layer Security Model
--------------------------

Authentication Methods
~~~~~~~~~~~~~~~~~~~~~~

JHE supports multiple authentication mechanisms for different user types:

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - User Type
     - Authentication Method
     - Implementation Details
   * - **Practitioners**
     - Email/password + optional SAML SSO
     - Django authentication with enterprise SSO integration
   * - **Patients**
     - Out-of-band invitation codes
     - Pre-generated OAuth codes with static PKCE
   * - **API Clients**
     - OAuth 2.0/OIDC with PKCE
     - Bearer tokens with configurable expiration
   * - **EHR Systems**
     - SMART on FHIR
     - Launch context with patient/encounter scope

Identity Management
~~~~~~~~~~~~~~~~~~~

**Supported Identity Providers:**

* **SAML 2.0 SSO** for enterprise authentication
* **OIDC providers** (Auth0, Okta, Azure AD)
* **Built-in Django authentication**
* **SMART on FHIR** for EHR integration

**Configuration Example (SAML):**

.. code-block:: bash

   # .env configuration
   SAML2_ENABLED=1
   SSO_VALID_DOMAINS=example.com,example.org
   IDENTITY_PROVIDER_METADATA_URL=https://idp.example.com/metadata

Role-Based Access Control (RBAC)
---------------------------------

JHE implements hierarchical RBAC with four permission levels:

Permission Matrix
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 15 15 15 15

   * - Permission
     - Super User
     - Manager
     - Member
     - Viewer
   * - View All Resources
     - ✅
     - ✅
     - ✅
     - ✅
   * - Create/Edit Studies
     - ✅
     - ✅
     - ✅
     - ❌
   * - Manage Patients
     - ✅
     - ✅
     - ✅
     - ❌
   * - Access Observations
     - ✅
     - ✅
     - ✅
     - ✅
   * - Manage Organization
     - ✅
     - ✅
     - ❌
     - ❌
   * - Manage Users/Roles
     - ✅
     - ✅
     - ❌
     - ❌
   * - System Administration
     - ✅
     - ❌
     - ❌
     - ❌

Organization Hierarchy
~~~~~~~~~~~~~~~~~~~~~~

Access control follows organizational structure:

.. code-block:: text

   Academic Medical Center (Root)
   ├── Department of Cardiology
   │   ├── Hypertension Study (Manager: Dr. Smith)
   │   │   ├── Members: Research Coordinators
   │   │   └── Viewers: Data Analysts
   │   └── Heart Failure Study
   └── Department of Endocrinology
       └── Diabetes Management Study

**Key Principles:**

* Permissions cascade down the hierarchy
* Users can have different roles in different organizations
* Study-specific access requires explicit enrollment
* Patient data access requires active consent

Data Security
-------------

Encryption
~~~~~~~~~~

**Data at Rest:**

* PostgreSQL transparent data encryption (TDE)
* AES-256 encryption for database backups
* Encrypted file storage for attachments

**Data in Transit:**

* TLS 1.2+ required for all connections
* HTTPS-only access (except localhost development)
* Certificate pinning for mobile apps

.. warning::

   Production deployments MUST use HTTPS on all non-localhost hosts due to browser security and OIDC requirements.

Secrets Management
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Best Practices for Secret Management

   # DON'T: Hard-code secrets
   SECRET_KEY = "hard-coded-secret"  # NEVER DO THIS

   # DO: Use environment variables
   import os
   SECRET_KEY = os.environ.get('SECRET_KEY')

   # DO: Use secret management services
   from azure.keyvault.secrets import SecretClient
   client = SecretClient(vault_url, credential)
   SECRET_KEY = client.get_secret("django-secret-key").value

**Required Secrets:**

* Django ``SECRET_KEY``
* Database credentials
* OIDC RS256 private key
* Patient authorization codes
* SAML certificates (if enabled)
* API keys for external services

Audit Logging
~~~~~~~~~~~~~

Comprehensive audit trail for all data access:

.. code-block:: json

   {
     "timestamp": "2025-01-15T10:30:00Z",
     "user": "practitioner@example.com",
     "action": "VIEW_OBSERVATION",
     "resource": "Observation/12345",
     "patient": "Patient/678",
     "study": "Study/42",
     "ip_address": "192.168.1.100",
     "user_agent": "Mozilla/5.0...",
     "result": "SUCCESS"
   }

**Audited Events:**

* Authentication attempts
* Data access (read/write)
* Consent changes
* Administrative actions
* API calls
* Failed authorization attempts

Regulatory Compliance
---------------------

HIPAA Compliance
~~~~~~~~~~~~~~~~~

JHE supports HIPAA covered entity requirements:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - HIPAA Requirement
     - JHE Implementation
   * - **Access Controls**
     - RBAC with organization/study scoping
   * - **Audit Controls**
     - Comprehensive logging with retention
   * - **Integrity**
     - FHIR validation, checksums, versioning
   * - **Transmission Security**
     - TLS 1.2+, encrypted APIs
   * - **Encryption**
     - At-rest and in-transit encryption

**Required HIPAA Controls:**

1. **Administrative Safeguards**

   * Security officer designation
   * Workforce training
   * Access management procedures
   * Incident response plan

2. **Physical Safeguards**

   * Facility access controls
   * Workstation security
   * Device and media controls

3. **Technical Safeguards**

   * Unique user identification
   * Automatic logoff
   * Encryption and decryption
   * Audit logs

GDPR Compliance
~~~~~~~~~~~~~~~

Supporting EU data protection requirements:

* **Right to Access**: Patient data export via API
* **Right to Erasure**: Data deletion workflows
* **Consent Management**: Granular consent tracking
* **Data Portability**: FHIR format exports
* **Privacy by Design**: Minimal data collection

21 CFR Part 11
~~~~~~~~~~~~~~~

For clinical trials and FDA submissions:

* **Electronic Signatures**: Authenticated timestamps
* **Audit Trails**: Immutable, time-stamped logs
* **Access Controls**: User authentication and authorization
* **Data Integrity**: Validation and error checking
* **System Validation**: Documented testing procedures

Security Best Practices
-----------------------

Development Security
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Django settings.py security configuration

   # Security headers
   SECURE_BROWSER_XSS_FILTER = True
   SECURE_CONTENT_TYPE_NOSNIFF = True
   X_FRAME_OPTIONS = 'DENY'

   # HTTPS enforcement
   SECURE_SSL_REDIRECT = True
   SESSION_COOKIE_SECURE = True
   CSRF_COOKIE_SECURE = True

   # Session security
   SESSION_EXPIRE_AT_BROWSER_CLOSE = True
   SESSION_COOKIE_HTTPONLY = True

   # HSTS (HTTP Strict Transport Security)
   SECURE_HSTS_SECONDS = 31536000
   SECURE_HSTS_INCLUDE_SUBDOMAINS = True
   SECURE_HSTS_PRELOAD = True

API Security
~~~~~~~~~~~~

**Rate Limiting:**

.. code-block:: python

   # Configure rate limiting
   REST_FRAMEWORK = {
       'DEFAULT_THROTTLE_CLASSES': [
           'rest_framework.throttling.AnonRateThrottle',
           'rest_framework.throttling.UserRateThrottle'
       ],
       'DEFAULT_THROTTLE_RATES': {
           'anon': '100/hour',
           'user': '1000/hour'
       }
   }

**Input Validation:**

.. code-block:: python

   # FHIR validation example
   from fhir.resources.observation import Observation

   def validate_observation(data):
       try:
           obs = Observation.parse_obj(data)
           # Additional custom validation
           if not has_consent(obs.subject, obs.code):
               raise PermissionError("No consent for this data type")
           return obs
       except ValidationError as e:
           log_security_event("INVALID_FHIR_DATA", details=str(e))
           raise

Network Security
~~~~~~~~~~~~~~~~

**Recommended Network Architecture:**

.. code-block:: text

   Internet
      │
   WAF/CDN (CloudFlare, AWS WAF)
      │
   Load Balancer (HTTPS termination)
      │
   ┌──────────────────────────┐
   │   DMZ / Public Subnet    │
   │  ┌────────────────────┐  │
   │  │  NGINX (Reverse    │  │
   │  │   Proxy + Rate     │  │
   │  │    Limiting)       │  │
   │  └────────────────────┘  │
   └──────────────────────────┘
              │
   ┌──────────────────────────┐
   │  Private Subnet          │
   │  ┌────────────────────┐  │
   │  │  JHE Application   │  │
   │  │   (Gunicorn)       │  │
   │  └────────────────────┘  │
   │           │              │
   │  ┌────────────────────┐  │
   │  │   PostgreSQL       │  │
   │  │   (No external     │  │
   │  │    access)         │  │
   │  └────────────────────┘  │
   └──────────────────────────┘

Operational Security
--------------------

Key Rotation Schedule
~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Component
     - Rotation Frequency
     - Method
   * - OIDC signing keys
     - 6 months
     - Generate new RS256 keypair
   * - Database passwords
     - 3 months
     - Coordinate with DBA team
   * - API keys
     - 12 months
     - Rolling deployment
   * - TLS certificates
     - Before expiration
     - Let's Encrypt auto-renewal
   * - Django SECRET_KEY
     - On suspicion of compromise
     - Blue-green deployment

Incident Response
~~~~~~~~~~~~~~~~~

**Incident Response Procedure:**

1. **Detection** → Security monitoring alerts
2. **Containment** → Isolate affected systems
3. **Assessment** → Determine scope and impact
4. **Notification** → Follow HIPAA breach notification rules
5. **Remediation** → Fix vulnerabilities
6. **Review** → Post-incident analysis

**HIPAA Breach Notification Timeline:**

* **Individuals**: Without unreasonable delay (max 60 days)
* **HHS**: Within 60 days
* **Media**: Within 60 days (if >500 individuals)

Security Monitoring
~~~~~~~~~~~~~~~~~~~

**Required Monitoring:**

* Failed authentication attempts
* Privilege escalation
* Unusual data access patterns
* API rate limit violations
* System resource usage
* Certificate expiration
* Database query performance

**Example Alert Configuration:**

.. code-block:: yaml

   # Alerting rules (Prometheus format)
   groups:
   - name: security
     rules:
     - alert: HighFailedAuthRate
       expr: rate(auth_failures[5m]) > 10
       annotations:
         summary: "High rate of authentication failures"

     - alert: UnusualDataAccess
       expr: data_access_rate > 3 * avg_over_time(data_access_rate[7d])
       annotations:
         summary: "Unusual spike in data access"

Compliance Checklist
--------------------

Pre-Production Security Review
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   ☐ TLS certificates configured and tested
   ☐ HTTPS-only enforcement enabled
   ☐ Secret management system configured
   ☐ Database encryption enabled
   ☐ Backup encryption configured
   ☐ Audit logging enabled and tested
   ☐ RBAC roles defined and assigned
   ☐ SAML/OIDC integration tested
   ☐ Rate limiting configured
   ☐ Security headers enabled
   ☐ Vulnerability scan completed
   ☐ Penetration test scheduled
   ☐ Incident response plan documented
   ☐ BAA agreements signed
   ☐ Security training completed

Ongoing Security Tasks
~~~~~~~~~~~~~~~~~~~~~~

**Daily:**
* Review security alerts
* Monitor failed authentications
* Check system health

**Weekly:**
* Review audit logs
* Update security patches
* Test backup restoration

**Monthly:**
* Access review for privileged users
* Security metrics review
* Vulnerability scanning

**Quarterly:**
* Full access audit
* Security training refresh
* Incident response drill
* Compliance assessment

**Annually:**
* Penetration testing
* Security policy review
* Risk assessment update
* Compliance audit

Next Steps
----------

* :doc:`configuration/security` - Configure security settings
* :doc:`architecture/omh-fhir-integration` - Understand data validation
* :doc:`workflow-guide` - Learn about consent management
* :doc:`api/index` - Secure API integration