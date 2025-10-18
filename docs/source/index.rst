.. JupyterHealth Exchange documentation master file

================================
JupyterHealth Exchange
================================

.. image:: https://img.shields.io/badge/FHIR-R5-orange
   :alt: FHIR R5

.. image:: https://img.shields.io/badge/Django-5.2-green
   :alt: Django 5.2

.. image:: https://img.shields.io/badge/Python-3.10+-blue
   :alt: Python 3.10+

About JupyterHealth
-------------------

`JupyterHealth <https://jupyterhealth.org/>`_ is an open-source, modular platform that eliminates healthcare data silos by integrating real-time health data from wearables, IoT devices, mobile apps, and Electronic Health Records (EHRs) into a unified, secure, and AI-powered ecosystem. The platform addresses the critical challenge of fragmented health data by providing researchers and clinicians with powerful tools for data integration, analysis, and visualization.

The JupyterHealth Ecosystem
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The complete JupyterHealth platform provides a comprehensive solution for healthcare data integration and analysis:

**Data Ingestion Capabilities**

* **Apple HealthKit Integration** - Direct connection to iOS health data
* **CommonHealth App** - Android platform for device data collection
* **Manufacturer Shims** - Direct integrations with common wearable manufacturers
* **EHR Interfaces** - Import clinical data from federally certified EHR platforms
* **IoT Device Support** - Real-time streaming from medical devices

**Data Standardization & Transformation**

* **Open mHealth Converters** - Standardize wearable data to OMH format
* **FHIR Transformers** - Convert diverse data to FHIR R5 standard
* **Custom Templates** - Community-contributed transformation pipelines

**Storage & Authentication**

* **JupyterHealth Exchange (JHE)** - SMART on FHIR server supporting:

  * FHIR resource storage
  * Open mHealth format preservation
  * Custom data formats
  * Granular consent management

* **Database Connectors** - Modules for common databases and data lakes
* **Authentication Services** - OIDC, SAML2, OAuth 2.0 integration

**Data Management & Analysis**

* **JupyterHub** - Scalable computational environment with:

  * Health data-specific libraries
  * Pre-configured analysis packages
  * Secure PHI handling capabilities
  * Community-contributed algorithms

**Presentation Layer**

* **Voilà Dashboards** - Interactive visualizations from Jupyter notebooks

  * Real-time clinical metrics displayed through web interfaces
  * Customizable charts for blood pressure, glucose, heart rate monitoring
  * Color-coded alerts based on clinical thresholds (AHA categories, UCSF goals)
  * Time-of-day analysis and trend visualization

* **SMART on FHIR Apps** - EHR-integrated clinical applications

  * Launch directly from patient context in Epic, Cerner, Allscripts
  * Provider-facing dashboards with consent-aware data access
  * Support for CDS Hooks and clinical decision support

* **Clinical Workflows** - Provider-specific interfaces

  * Role-based views (physician, nurse, researcher)
  * Study enrollment and consent management tools
  * Observation review and annotation capabilities

* **Patient Portals** - Consumer health engagement

  * Personal health data visualization
  * Device connection management
  * Consent and data sharing preferences

About This Documentation
------------------------

This documentation focuses on deploying **JupyterHealth Exchange**, the core storage and authentication platform that enables secure, consent-based health data sharing across all JupyterHealth components.

**Target Audience**: Enterprise IT (infrastructure, security, networking), Research IT, Clinical Informatics, Compliance/Privacy, DevOps/SRE, Data Governance, and project leadership at academic medical centers.

**Purpose**: Provide a comprehensive playbook to stand up and operate the JupyterHealth Exchange Django web app in a production environment, with options for on-prem, containers, and managed cloud (AWS/Azure). This guide combines strategic context with tactical implementation details following healthcare IT best practices.

.. grid:: 1 1 2 3
    :gutter: 2

    .. grid-item-card:: 💡 Solving the Problem
        :link: solving-the-problem
        :link-type: doc

        Understanding the healthcare data challenge and our solution

    .. grid-item-card:: ⚙️ Core Platform Components
        :link: core-platform-components
        :link-type: doc

        Technical architecture, data flows, and key stakeholders

    .. grid-item-card:: 🚀 Quick Start
        :link: quickstart
        :link-type: doc

        Get JHE running in minutes with our quick start guide

    .. grid-item-card:: 📋 Workflow Guide
        :link: workflow-guide
        :link-type: doc

        Step-by-step guide from study setup to data presentation

    .. grid-item-card:: 📦 Installation
        :link: installation/index
        :link-type: doc

        Complete guide from zero to production deployment

    .. grid-item-card:: 🔧 Configuration
        :link: configuration/index
        :link-type: doc

        Configure authentication, databases, and security settings

    .. grid-item-card:: 📡 API Reference
        :link: api/index
        :link-type: doc

        REST, FHIR R5, and OAuth 2.0 API documentation

    .. grid-item-card:: 🏗️ OMH-FHIR Integration
        :link: architecture/omh-fhir-integration
        :link-type: doc

        Hybrid approach for device data and healthcare interoperability

    .. grid-item-card:: 🔒 Security & Compliance
        :link: security-compliance
        :link-type: doc

        HIPAA compliance, RBAC, identity management, and security best practices

Key Features
------------

✅ **FHIR R5 Compliant** - Full support for Patient and Observation resources

✅ **Consent Management** - Granular, patient-controlled data sharing

✅ **Multi-source Integration** - Apple HealthKit, CommonHealth, wearables, EHRs

✅ **Open mHealth Support** - Standardized device data with OMH schemas

✅ **SMART on FHIR** - Deploy apps directly in EHR platforms

✅ **Enterprise Ready** - SAML SSO, audit logging, HIPAA controls

Why JupyterHealth Exchange?
---------------------------

Clinical research faces a fundamental challenge: researchers need real-world patient health data from personal devices, but there's no standardized, consent-aware infrastructure to facilitate this exchange.

JHE solves this by providing:

* **Patient-controlled data sharing** via explicit consent management
* **Device-agnostic collection** using Open mHealth standards
* **Healthcare interoperability** through FHIR R5 compliance
* **Privacy-first architecture** with granular consent controls

.. toctree::
   :maxdepth: 2
   :caption: Overview
   :hidden:

   solving-the-problem
   core-platform-components
   security-compliance

.. toctree::
   :maxdepth: 2
   :caption: Getting Started
   :hidden:

   quickstart
   workflow-guide
   installation/index
   configuration/index

.. toctree::
   :maxdepth: 2
   :caption: User Guide
   :hidden:

   user-guide/organizations
   user-guide/studies
   user-guide/patients
   user-guide/consents

.. toctree::
   :maxdepth: 2
   :caption: API Documentation
   :hidden:

   api/index
   api/fhir
   api/oauth

.. toctree::
   :maxdepth: 2
   :caption: Administration
   :hidden:

   admin/deployment
   admin/monitoring
   admin/backup
   admin/troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Architecture
   :hidden:

   architecture/omh-fhir-integration

.. toctree::
   :maxdepth: 1
   :caption: Resources
   :hidden:

   support
   contributing
   changelog

Getting Help
------------

* **GitHub Issues**: `Report bugs or request features <https://github.com/jupyterhealth/jupyterhealth-exchange/issues>`_
* **Documentation**: You're reading it!
* **Community**: Join the JupyterHealth community

License
-------

JupyterHealth Exchange is open source software licensed under the Apache 2.0 License.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`