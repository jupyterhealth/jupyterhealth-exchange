==================
API Documentation
==================

JupyterHealth Exchange provides comprehensive APIs for health data management, including REST APIs for administration, FHIR R5 endpoints for healthcare interoperability, and OAuth 2.0 for secure authentication.

.. toctree::
   :maxdepth: 2
   :hidden:

   fhir
   oauth
   examples

Interactive Documentation
-------------------------

JHE provides interactive API documentation through:

* **Swagger UI**: ``https://jhe.yourdomain.org/api/schema/swagger-ui/``
* **ReDoc**: ``https://jhe.yourdomain.org/api/schema/redoc/``
* **OpenAPI Schema**: ``https://jhe.yourdomain.org/api/schema/``

Authentication
--------------

All API endpoints require authentication via OAuth 2.0 Bearer tokens::

    curl -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
         https://jhe.yourdomain.org/api/v1/users/profile

API Overview
------------

.. list-table::
   :header-rows: 1
   :widths: 30 40 30

   * - API Type
     - Purpose
     - Base URL
   * - REST API
     - Administrative operations, study management
     - ``/api/v1/``
   * - FHIR R5
     - Healthcare data exchange, clinical resources
     - ``/fhir/r5/``
   * - OAuth 2.0
     - Authentication and authorization
     - ``/o/``

Rate Limiting
-------------

API endpoints implement rate limiting to ensure fair usage:

* **Anonymous**: 100 requests/hour
* **Authenticated**: 1000 requests/hour
* **Batch uploads**: 100 observations per bundle

Rate limit information is provided in response headers::

    X-RateLimit-Limit: 1000
    X-RateLimit-Remaining: 995
    X-RateLimit-Reset: 1711234567

Error Handling
--------------

All APIs follow standard HTTP status codes and return errors in consistent format:

.. code-block:: json

    {
      "error": "invalid_request",
      "error_description": "Missing required parameter: patient_id"
    }

Common status codes:

* **200 OK** - Successful request
* **201 Created** - Resource created successfully
* **400 Bad Request** - Invalid request parameters
* **401 Unauthorized** - Missing or invalid authentication
* **403 Forbidden** - Insufficient permissions
* **404 Not Found** - Resource not found
* **422 Unprocessable Entity** - FHIR validation error
* **429 Too Many Requests** - Rate limit exceeded
* **500 Internal Server Error** - Server error

REST API Reference
==================

The JupyterHealth Exchange REST API provides endpoints for managing organizations, studies, patients, and observations.

Base URL
--------

All REST API endpoints are available at::

    https://jhe.yourdomain.org/api/v1/

Organizations
-------------

List Organizations
~~~~~~~~~~~~~~~~~~

.. http:get:: /api/v1/organizations

   Returns a paginated list of organizations accessible to the user.

   **Example Request**::

       GET /api/v1/organizations HTTP/1.1
       Host: jhe.yourdomain.org
       Authorization: Bearer <token>

   **Example Response**:

   .. code-block:: json

      {
        "count": 2,
        "next": null,
        "previous": null,
        "results": [
          {
            "id": 1,
            "name": "UC Berkeley",
            "type": "academic_medical_center",
            "partOf": null,
            "currentUserRole": "manager",
            "children": [
              {
                "id": 2,
                "name": "Berkeley Cardiology",
                "type": "department",
                "partOf": 1
              }
            ]
          }
        ]
      }

   :statuscode 200: Success
   :statuscode 401: Unauthorized

Create Organization
~~~~~~~~~~~~~~~~~~~

.. http:post:: /api/v1/organizations

   Creates a new organization.

   **Example Request**:

   .. code-block:: json

      {
        "name": "New Research Center",
        "type": "research_institution",
        "partOf": 1
      }

   **Example Response**:

   .. code-block:: json

      {
        "id": 3,
        "name": "New Research Center",
        "type": "research_institution",
        "partOf": 1,
        "currentUserRole": "manager"
      }

   :statuscode 201: Created
   :statuscode 400: Invalid data
   :statuscode 403: Insufficient permissions

Studies
-------

List Studies
~~~~~~~~~~~~

.. http:get:: /api/v1/studies

   Returns studies accessible to the current user.

   **Query Parameters**:

   * ``organization`` - Filter by organization ID
   * ``page`` - Page number for pagination
   * ``page_size`` - Number of items per page

   **Example Response**:

   .. code-block:: json

      {
        "count": 3,
        "results": [
          {
            "id": 1,
            "name": "Blood Pressure Monitoring Study",
            "identifier": "BP-2024-001",
            "organization": 1,
            "organizationName": "UC Berkeley",
            "description": "Remote monitoring of hypertension patients",
            "createdAt": "2024-01-15T10:00:00Z",
            "patientCount": 45,
            "observationCount": 3250
          }
        ]
      }

Create Study
~~~~~~~~~~~~

.. http:post:: /api/v1/studies

   Creates a new study within an organization.

   **Request Body**:

   .. code-block:: json

      {
        "name": "Diabetes CGM Study",
        "identifier": "CGM-2024-001",
        "organization": 1,
        "description": "Continuous glucose monitoring for T2D patients"
      }

   :statuscode 201: Study created
   :statuscode 400: Invalid data
   :statuscode 403: Not authorized for organization

Add Patient to Study
~~~~~~~~~~~~~~~~~~~~

.. http:post:: /api/v1/studies/{id}/patients

   Adds a patient to a study with requested data scopes.

   **Request Body**:

   .. code-block:: json

      {
        "patientId": 101,
        "scopes": ["blood-pressure", "heart-rate"]
      }

   **Response**:

   .. code-block:: json

      {
        "studyId": 1,
        "patientId": 101,
        "scopesRequested": ["blood-pressure", "heart-rate"],
        "invitationLink": "https://jhe.yourdomain.org/invite/abc123def456"
      }

Patients
--------

Create Patient
~~~~~~~~~~~~~~

.. http:post:: /api/v1/patients

   Creates a new patient record.

   **Request Body**:

   .. code-block:: json

      {
        "nameFamily": "Johnson",
        "nameGiven": "Mary",
        "telecomEmail": "mary.johnson@email.com",
        "birthDate": "1980-06-22",
        "gender": "female",
        "organizations": [1]
      }

   :statuscode 201: Patient created
   :statuscode 400: Invalid data
   :statuscode 409: Email already exists

Get Patient Consents
~~~~~~~~~~~~~~~~~~~~

.. http:get:: /api/v1/patients/{id}/consents

   Returns consent information for a patient.

   **Response**:

   .. code-block:: json

      {
        "studies": [
          {
            "id": 1,
            "name": "Blood Pressure Study",
            "consents": [
              {
                "scope": "blood-pressure",
                "status": "active",
                "dateTime": "2024-01-20T09:00:00Z",
                "provision": "permit"
              }
            ]
          }
        ],
        "studiesPendingConsent": [
          {
            "id": 2,
            "name": "Heart Rate Variability Study",
            "scopesRequested": ["heart-rate", "rr-interval"]
          }
        ]
      }

Observations
------------

List Observations
~~~~~~~~~~~~~~~~~

.. http:get:: /api/v1/observations

   Returns observations based on filter criteria.

   **Query Parameters**:

   * ``patient`` - Patient ID
   * ``study`` - Study ID
   * ``code`` - Observation code (e.g., blood-pressure)
   * ``date_from`` - Start date (ISO 8601)
   * ``date_to`` - End date (ISO 8601)

   **Example**::

       GET /api/v1/observations?patient=101&code=blood-pressure

   :statuscode 200: Success
   :statuscode 403: No consent for requested data

Pagination
----------

List endpoints support pagination using query parameters:

* ``page`` - Page number (default: 1)
* ``page_size`` - Items per page (default: 20, max: 100)

Response includes pagination metadata:

.. code-block:: json

   {
     "count": 245,
     "next": "https://jhe.yourdomain.org/api/v1/patients?page=3",
     "previous": "https://jhe.yourdomain.org/api/v1/patients?page=1",
     "results": [...]
   }

Filtering
---------

Common filter parameters across endpoints:

* ``search`` - Text search across relevant fields
* ``organization`` - Filter by organization ID
* ``study`` - Filter by study ID
* ``created_after`` - Records created after date
* ``created_before`` - Records created before date
* ``ordering`` - Sort results (e.g., ``-created_at`` for descending)

Next Steps
----------

* :doc:`fhir` - Learn about FHIR R5 integration
* :doc:`oauth` - Understand authentication flows
* :doc:`examples` - See practical API usage examples