==============
Workflow Guide
==============

This guide walks through the complete end-to-end workflow of using JupyterHealth Exchange, from study setup through data collection and presentation.

.. contents:: Workflow Phases
   :local:
   :depth: 2

Phase 1: Study Setup
--------------------

The research team initiates a new study in JupyterHealth Exchange:

1. **Researcher creates study** in JupyterHealth Exchange

   * Logs into JHE web portal with appropriate permissions
   * Creates new study under their organization
   * Assigns unique study identifier

2. **Defines data requirements** (blood glucose, heart rate, etc.)

   * Selects from available data types (vitals, activity, nutrition)
   * Specifies measurement frequency and duration
   * Sets data quality thresholds

3. **Configures data sources** (supported devices/apps)

   * Enables compatible device integrations
   * Configures API connections for specific manufacturers
   * Sets up data transformation pipelines

4. **Generates patient invitations** with OAuth codes

   * Creates unique invitation links for each participant
   * Configures consent requirements
   * Sets data access permissions

Phase 2: Patient Enrollment
----------------------------

Patients join the study and configure their data sharing:

1. **Patient receives invitation** from research team

   * Via secure email link
   * Through in-clinic QR code
   * Direct link from patient portal

2. **Chooses data sharing method**:

   .. list-table::
      :header-rows: 1
      :widths: 30 70

      * - Method
        - Description
      * - **Mobile Apps**
        - CommonHealth for Android, HealthKit connector for iOS
      * - **EHR Connection**
        - Direct connection via patient portal using SMART on FHIR
      * - **Web Upload**
        - Manual data entry through secure web interface

3. **Reviews study requirements** and data types requested

   * Views detailed study information
   * Understands data collection frequency
   * Reviews privacy and security measures

4. **Grants granular consent** for specific data types

   * Selects which data types to share
   * Sets sharing duration
   * Can modify or revoke consent at any time

5. **Connects data sources**:

   * **Wearable devices**: Fitbit, Apple Watch, Garmin, etc.
   * **Medical devices**: Glucose monitors, BP cuffs, scales
   * **Health apps**: MyFitnessPal, Strava, health record apps
   * **EHR portals**: Epic MyChart, Cerner, Allscripts

Phase 3: Data Collection
------------------------

Automated data collection and processing:

1. **Data source generates measurement**

   * Device records glucose reading
   * Wearable tracks steps and heart rate
   * BP cuff measures blood pressure

2. **Ingestion layer processes data**:

   .. code-block:: text

      Device Shims ──────► Manufacturer APIs
                           │
      Mobile Apps ───────► Device SDKs
                           │
      EHR Interfaces ────► FHIR Bulk Export
                           │
                           ▼
                    JupyterHealth Exchange

3. **Standardization occurs**:

   .. list-table::
      :header-rows: 1
      :widths: 30 30 40

      * - Source Type
        - Standard Format
        - Example
      * - Wearable data
        - Open mHealth
        - Heart rate → OMH heart-rate schema
      * - Clinical data
        - FHIR R5
        - Lab results → Observation resource
      * - Custom data
        - Preserved + metadata
        - Proprietary device → JSON with context

4. **Data flows to JHE** via authenticated APIs

   * OAuth 2.0 secured transmission
   * TLS encryption in transit
   * Rate limiting and throttling

5. **Exchange validates consent** before accepting data

   * Checks active consent for patient/study pair
   * Verifies data type permissions
   * Validates against consent duration

6. **Stores validated data** with patient/study associations

   * PostgreSQL with JSONB storage
   * Maintains full audit trail
   * Indexes for efficient querying

Phase 4: Data Access & Presentation
------------------------------------

Multiple stakeholders access and visualize the collected data:

Researchers Access Data
~~~~~~~~~~~~~~~~~~~~~~~~

**Available channels:**

* **JHE Web Portal** - Study management dashboard
* **FHIR APIs** - Programmatic access for analysis
* **REST APIs** - Administrative operations
* **Bulk Export** - Large-scale data downloads

**Example researcher workflow:**

.. code-block:: python

   # Access via FHIR API
   observations = fhir_client.search(
       resource_type="Observation",
       search_params={
           "patient": "Patient/123",
           "code": "85354-9",  # Blood pressure
           "date": "ge2024-01-01"
       }
   )

Clinicians View Insights
~~~~~~~~~~~~~~~~~~~~~~~~~

**Access methods:**

* **SMART on FHIR apps** launched from EHR
* **Clinical dashboards** with real-time patient data
* **Alerts and notifications** for critical values
* **Integrated reports** in clinical workflows

**Example clinical dashboard features:**

.. list-table::
   :header-rows: 1

   * - Feature
     - Description
   * - Real-time monitoring
     - Live updates as data arrives
   * - Trend analysis
     - Historical patterns and predictions
   * - Alert thresholds
     - Customizable clinical boundaries
   * - Export capabilities
     - PDF reports for chart inclusion

Patients Engage Through
~~~~~~~~~~~~~~~~~~~~~~~~

**Patient interfaces:**

* **Personal health dashboards** - View own data trends
* **Mobile app visualizations** - On-the-go access
* **Exported reports** - Share with providers
* **Consent management** - Control data sharing

Data Presentation Options
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. grid:: 1 1 2 2
    :gutter: 2

    .. grid-item-card:: Voilà Dashboards

        Interactive visualizations from Jupyter notebooks

        * Real-time updates
        * Custom charts and graphs
        * Clinical decision support

    .. grid-item-card:: SMART Launch Apps

        EHR-integrated applications

        * Epic, Cerner, Allscripts
        * Provider workflows
        * Patient context aware

    .. grid-item-card:: Custom Visualizations

        Tailored to clinical needs

        * Department-specific views
        * Research protocols
        * Quality metrics

    .. grid-item-card:: API Integrations

        Third-party connections

        * Analytics platforms
        * Research tools
        * Reporting systems

Best Practices
--------------

Study Setup
~~~~~~~~~~~

* **Clear consent language** - Ensure patients understand data usage
* **Minimal data collection** - Only request necessary data types
* **Regular communication** - Keep participants informed of progress

Data Quality
~~~~~~~~~~~~

* **Validation rules** - Implement checks for data accuracy
* **Missing data handling** - Define protocols for gaps
* **Device calibration** - Ensure measurement accuracy

Security & Privacy
~~~~~~~~~~~~~~~~~~

* **Regular audits** - Review access logs and permissions
* **Data minimization** - Store only required information
* **Encryption everywhere** - At rest and in transit

Patient Engagement
~~~~~~~~~~~~~~~~~~

* **User-friendly interfaces** - Simple, intuitive designs
* **Regular feedback** - Show patients their contribution value
* **Support channels** - Provide help for technical issues

Common Use Cases
----------------

Remote Patient Monitoring
~~~~~~~~~~~~~~~~~~~~~~~~~~

Monitor chronic disease patients between visits:

1. Configure study for hypertension management
2. Enroll patients with home BP monitors
3. Collect daily readings automatically
4. Alert clinicians to out-of-range values
5. Adjust medications based on trends

Clinical Trial Data Collection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Gather real-world evidence for research:

1. Define trial protocol in JHE
2. Recruit participants across multiple sites
3. Collect device data continuously
4. Combine with EHR clinical data
5. Analyze in JupyterHub notebooks

Population Health Analytics
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Aggregate anonymized data for insights:

1. Create population-level study
2. Collect de-identified data
3. Apply analytics pipelines
4. Generate public health reports
5. Inform policy decisions

Next Steps
----------

* :doc:`quickstart` - Get started with your first study
* :doc:`api/index` - Integrate with APIs
* :doc:`configuration/index` - Configure for your environment
* :doc:`core-platform-components` - Understand the architecture