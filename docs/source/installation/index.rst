============
Installation
============

This section covers complete installation of JupyterHealth Exchange from development to production deployment.

.. toctree::
   :maxdepth: 2

   zero-to-prod
   docker
   cloud

Installation Options
--------------------

JupyterHealth Exchange can be deployed in several ways:

**Traditional Server Installation**
   Best for on-premise deployments with existing infrastructure.
   See :doc:`zero-to-prod` for complete guide.

**Docker Deployment**
   Containerized deployment for consistency across environments.
   See :doc:`docker` for Docker and Docker Compose setup.

**Cloud Deployment**
   Managed cloud services (AWS, Azure) for scalability.
   See :doc:`cloud` for cloud-specific guides.

System Requirements
-------------------

Minimum Hardware
~~~~~~~~~~~~~~~~

* **CPU**: 4 cores (8 recommended for production)
* **RAM**: 8GB minimum (16GB recommended)
* **Storage**: 50GB for application and initial data
* **Network**: Static IP with HTTPS capability

Software Dependencies
~~~~~~~~~~~~~~~~~~~~~

* **Operating System**: Linux (Ubuntu 20.04/22.04 LTS) or macOS
* **Python**: 3.10, 3.11, 3.12, or 3.13
* **PostgreSQL**: 14+ (with JSONB support)
* **NGINX**: Reverse proxy (production)
* **Git**: For repository access

Quick Decision Guide
--------------------

.. list-table::
   :header-rows: 1
   :widths: 25 25 25 25

   * - Deployment Type
     - Best For
     - Complexity
     - Maintenance
   * - Traditional Server
     - On-premise, single site
     - Medium
     - Manual updates
   * - Docker
     - Consistent deployments
     - Low
     - Container management
   * - AWS/Azure
     - Scalability, managed services
     - Low
     - Cloud console
   * - Kubernetes
     - Large scale, multi-region
     - High
     - GitOps/automation

Security Considerations
-----------------------

Before deploying to production:

* ✅ Generate new RSA keys for OIDC
* ✅ Set strong database passwords
* ✅ Configure SSL certificates
* ✅ Enable firewall rules
* ✅ Set up backup strategy
* ✅ Configure audit logging
* ✅ Review Django security settings

Next Steps
----------

1. Choose your deployment method
2. Follow the appropriate installation guide
3. Configure authentication (:doc:`../configuration/authentication`)
4. Set up monitoring (:doc:`../admin/monitoring`)
5. Create your first organization and study