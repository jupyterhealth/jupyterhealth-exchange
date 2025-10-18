========================
Core Platform Components
========================

**JupyterHealth Exchange (JHE)** is a standalone, consent-aware health data exchange platform that enables secure, standardized health data collection, storage, and sharing for research and clinical purposes.

Data Collection & Storage
-------------------------

JHE provides a robust foundation for managing health data:

* **Django 5.2 web application** managing consent and data flow
* **FHIR R5 API** with schema validation using `fhir.resources <https://github.com/glichtner/fhir.resources>`_
* **Open mHealth schema validation** for device data standardization
* **PostgreSQL database** (required for JSON functions and JSONB storage)
* **Granular consent management** per data type and study

Data Access & Management
------------------------

Built-in Lightweight Frontend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

JHE includes a lightweight Vanilla JS single-page application (no npm required) using:

* `oidc-client-ts <https://github.com/authts/oidc-client-ts>`_ for authentication
* `handlebars <https://github.com/handlebars-lang/handlebars.js>`_ for templating
* `bootstrap <https://github.com/twbs/bootstrap>`_ for styling

API and Authentication
~~~~~~~~~~~~~~~~~~~~~~

* **REST APIs** using `Django Rest Framework <https://github.com/encode/django-rest-framework>`_
* **OAuth 2.0, OIDC and SMART on FHIR** using `django-oauth-toolkit <https://github.com/jazzband/django-oauth-toolkit>`_
* **Role-based access control (RBAC)** with four permission levels:

  - **Manager**: Full administrative control
  - **Member**: Read/write access to studies
  - **Viewer**: Read-only access
  - **Patient**: Self-service data management

* **Organization and study hierarchy management**
* **Patient invitation and enrollment system**

Data Ingestion Methods
----------------------

Mobile Apps
~~~~~~~~~~~

* `CommonHealth Android App <https://play.google.com/store/apps/details?id=org.thecommonsproject.android.phr>`_ - Full device integration
* **Apple HealthKit connectors** - iOS health data access
* **Custom mobile SDKs** - Direct app integrations

Wearable Device Shims
~~~~~~~~~~~~~~~~~~~~~

Direct integrations with popular health devices:

* **iHealth devices** (glucose monitors, blood pressure cuffs)
* **Dexcom** continuous glucose monitors
* **Fitbit, Garmin**, and other fitness trackers
* **Direct manufacturer API integrations**

EHR Integrations
~~~~~~~~~~~~~~~~

Healthcare system interoperability through:

* **SMART on FHIR launch protocol**
* **Federally mandated FHIR APIs (USCDI)**
* **HL7 FHIR bulk data export**
* **Direct database connections** (with appropriate BAAs)

Authentication Methods
~~~~~~~~~~~~~~~~~~~~~~

Multiple authentication options for different use cases:

* **OAuth 2.0/OIDC** for API access
* **SAML2 SSO** for enterprise authentication
* **Patient invitation codes** for mobile apps
* **SMART on FHIR** for EHR launch

Data Standardization
--------------------

All incoming data is transformed to standard formats:

.. grid:: 1 1 2 2
    :gutter: 2

    .. grid-item-card:: Open mHealth

        For wearable and device data using industry-standard schemas

    .. grid-item-card:: FHIR R5

        For clinical data and healthcare interoperability

    .. grid-item-card:: Custom Formats

        Preserved with appropriate metadata for specialized devices

    .. grid-item-card:: Community Templates

        Reusable transformation pipelines contributed by the community

Key Stakeholders
----------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Stakeholder
     - Role & Responsibilities
   * - **Patients/Study Participants**
     - Own personal health devices, control data sharing through granular consent
   * - **Researchers/Practitioners**
     - Need patient data for studies, perform analysis using consented data
   * - **Healthcare Organizations**
     - Host studies, manage compliance, ensure data governance
   * - **Device Manufacturers**
     - Provide health monitoring devices (integrated via CommonHealth and direct APIs)

Technical Architecture
----------------------

.. code-block:: text

    ┌─────────────────────────────────────────────────────────────┐
    │                      Data Sources                           │
    ├──────────────┬──────────────┬──────────────┬──────────────┤
    │ Mobile Apps  │   Wearables  │     EHRs     │  IoT Devices │
    └──────┬───────┴──────┬───────┴──────┬───────┴──────┬───────┘
           │              │              │              │
           ▼              ▼              ▼              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                    JupyterHealth Exchange                   │
    ├─────────────────────────────────────────────────────────────┤
    │  ┌─────────────────────────────────────────────────────┐   │
    │  │              Data Ingestion Layer                   │   │
    │  │  • OAuth 2.0/OIDC Authentication                   │   │
    │  │  • SMART on FHIR Protocol                         │   │
    │  │  • Device API Connectors                          │   │
    │  └─────────────────────────────────────────────────────┘   │
    │  ┌─────────────────────────────────────────────────────┐   │
    │  │           Data Standardization Layer               │   │
    │  │  • Open mHealth Converters                        │   │
    │  │  • FHIR R5 Transformers                          │   │
    │  │  • Custom Format Handlers                        │   │
    │  └─────────────────────────────────────────────────────┘   │
    │  ┌─────────────────────────────────────────────────────┐   │
    │  │            Storage & Consent Layer                 │   │
    │  │  • PostgreSQL with JSONB                         │   │
    │  │  • Granular Consent Management                   │   │
    │  │  • Audit Logging                                │   │
    │  └─────────────────────────────────────────────────────┘   │
    │  ┌─────────────────────────────────────────────────────┐   │
    │  │              Data Access Layer                     │   │
    │  │  • REST APIs (Django Rest Framework)             │   │
    │  │  • FHIR R5 APIs                                 │   │
    │  │  • GraphQL (Optional)                           │   │
    │  └─────────────────────────────────────────────────────┘   │
    └─────────────────────────────────────────────────────────────┘
           │              │              │              │
           ▼              ▼              ▼              ▼
    ┌──────────────┬──────────────┬──────────────┬──────────────┐
    │  Researchers │  Clinicians  │   Patients   │  Analytics   │
    └──────────────┴──────────────┴──────────────┴──────────────┘

Next Steps
----------

* :doc:`installation/index` - Deploy JHE in your environment
* :doc:`configuration/index` - Configure authentication and databases
* :doc:`api/index` - Explore the API documentation
* :doc:`quickstart` - Get started quickly with JHE