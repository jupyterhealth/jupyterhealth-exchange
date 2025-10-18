=============
Quick Start
=============

This guide will get you running JupyterHealth Exchange in development mode in under 15 minutes.

Prerequisites
-------------

Before you begin, ensure you have:

* Python 3.10, 3.11, 3.12, or 3.13
* PostgreSQL 14+
* Git

Quick Installation
------------------

1. **Clone the repository**::

    git clone https://github.com/jupyterhealth/jupyterhealth-exchange.git
    cd jupyterhealth-exchange/jhe

2. **Install dependencies**::

    pip install pipenv
    pipenv install

3. **Set up PostgreSQL**::

    sudo -u postgres createdb jhe_dev
    sudo -u postgres createuser jheuser -P
    # Enter password when prompted

4. **Configure environment**::

    cp dot_env_example.txt .env
    # Edit .env with your database credentials

5. **Initialize database**::

    pipenv shell
    python manage.py migrate
    python manage.py seed  # Load sample data

6. **Create admin user**::

    python manage.py createsuperuser

7. **Run the server**::

    python manage.py runserver

8. **Access the application**:

   * Portal: http://localhost:8000
   * Admin: http://localhost:8000/admin
   * API: http://localhost:8000/api/v1/
   * Swagger: http://localhost:8000/api/schema/swagger-ui/

Default Test Users
------------------

The ``seed`` command creates these test users:

.. list-table::
   :header-rows: 1
   :widths: 20 30 20 30

   * - Username
     - Email
     - Password
     - Role
   * - sam
     - sam@example.com
     - password123
     - Super User
   * - mary
     - mary@example.com
     - password123
     - Manager
   * - megan
     - megan@example.com
     - password123
     - Member
   * - victor
     - victor@example.com
     - password123
     - Viewer

Test Patients
-------------

Sample patients are also created:

* Peter Patient (peter@example.com)
* Pamela Patient (pamela@example.com)
* Percy Patient (percy@example.com)

Next Steps
----------

Now that you have JHE running:

1. :doc:`installation/index` - Set up production deployment
2. :doc:`configuration/index` - Configure authentication and security
3. :doc:`api/index` - Explore the API documentation
4. :doc:`user-guide/studies` - Create your first study

Common Issues
-------------

**PostgreSQL Connection Error**
   Ensure PostgreSQL is running and credentials in ``.env`` match your database setup.

**Port Already in Use**
   Another service is using port 8000. Run on a different port::

       python manage.py runserver 8080

**Missing Dependencies**
   Make sure you're in the pipenv shell::

       pipenv shell
       python manage.py runserver

For more troubleshooting, see :doc:`admin/troubleshooting`.