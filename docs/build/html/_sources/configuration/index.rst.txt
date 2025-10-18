==============
Configuration
==============

This section covers configuration of JupyterHealth Exchange for production deployment.

.. toctree::
   :maxdepth: 2

   environment
   authentication
   database
   security

Configuration Overview
----------------------

JHE uses environment variables for configuration, following the 12-factor app methodology. All settings are configured through a ``.env`` file in the application root.

Quick Start Configuration
-------------------------

1. Copy the example configuration::

       cp dot_env_example.txt .env

2. Edit ``.env`` with your settings::

       vim .env

3. At minimum, configure:

   * Database credentials
   * Site URL
   * OIDC keys (generate new ones!)
   * Admin email

Critical Security Settings
--------------------------

.. warning::

   **Never use default keys in production!**

   You MUST generate new values for:

   * ``OIDC_RSA_PRIVATE_KEY``
   * ``PATIENT_AUTHORIZATION_CODE_CHALLENGE``
   * ``PATIENT_AUTHORIZATION_CODE_VERIFIER``
   * ``OIDC_CLIENT_ID``

Generate secure keys using the provided scripts or OpenSSL commands.

Configuration Files
-------------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - File
     - Purpose
   * - ``.env``
     - Primary configuration (database, auth, site settings)
   * - ``settings.py``
     - Django settings (usually no edits needed)
   * - ``nginx.conf``
     - Web server configuration (production)
   * - ``gunicorn.conf.py``
     - Application server settings (optional)

Environment-Specific Settings
-----------------------------

Development
~~~~~~~~~~~

* ``DEBUG=True`` (shows detailed errors)
* ``SITE_URL=http://localhost:8000``
* Can use SQLite for testing

Staging
~~~~~~~

* ``DEBUG=False``
* ``SITE_URL=https://staging.jhe.yourdomain.org``
* Use PostgreSQL
* Enable SAML2 testing

Production
~~~~~~~~~~

* ``DEBUG=False`` (CRITICAL!)
* ``SITE_URL=https://jhe.yourdomain.org``
* PostgreSQL with backups
* SAML2/SSO enabled
* Audit logging active
* Monitoring configured

Next Steps
----------

1. :doc:`environment` - Configure environment variables
2. :doc:`authentication` - Set up OIDC and SAML
3. :doc:`database` - Configure PostgreSQL
4. :doc:`security` - Production security settings