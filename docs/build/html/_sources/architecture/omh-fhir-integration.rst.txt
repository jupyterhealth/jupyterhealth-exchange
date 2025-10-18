=====================
OMH-FHIR Integration
=====================

JupyterHealth Exchange implements a unique hybrid approach that embeds Open mHealth (OMH) data within FHIR resources, combining the best of both standards for optimal healthcare interoperability and device data fidelity.

Why Open mHealth Inside FHIR?
------------------------------

This hybrid approach solves multiple challenges that neither standard addresses completely on its own:

.. list-table:: Standards Comparison
   :header-rows: 1
   :widths: 25 20 20 35

   * - Challenge
     - FHIR Alone
     - OMH Alone
     - Our Hybrid Solution
   * - Healthcare interoperability
     - ✅ Excellent
     - ❌ Limited
     - ✅ Full FHIR compliance
   * - Device data fidelity
     - ⚠️ Lossy
     - ✅ Complete
     - ✅ Preserves all device data
   * - Validation
     - ⚠️ Basic
     - ✅ JSON Schema
     - ✅ Two-tier validation
   * - Query capabilities
     - ✅ Standardized
     - ❌ Custom
     - ✅ FHIR queries + JSON indexing

Key Benefits
~~~~~~~~~~~~

**1. Healthcare Compatibility**
   - Works with any FHIR-compliant system
   - Maintains standard FHIR query patterns
   - Integrates with existing EHR infrastructure

**2. Device Data Preservation**
   - No loss of precision or metadata
   - Preserves manufacturer-specific fields
   - Maintains temporal relationships

**3. Dual Validation**
   - FHIR resource validation ensures structure
   - OMH schema validation ensures content
   - Catches errors at multiple levels

**4. Future-Proof Design**
   - New device types easily added
   - Schema evolution without breaking changes
   - Backward compatibility maintained

Data Structure
--------------

The integration pattern embeds Open mHealth data as Base64-encoded JSON within a FHIR Observation's ``valueAttachment`` field:

FHIR Observation Wrapper
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: json

   {
     "resourceType": "Observation",
     "id": "obs-123",
     "status": "final",
     "subject": {
       "reference": "Patient/123"
     },
     "code": {
       "coding": [{
         "system": "https://w3id.org/openmhealth",
         "code": "omh:blood-glucose:4.0",
         "display": "Blood Glucose"
       }]
     },
     "effectiveDateTime": "2025-01-15T10:30:00Z",
     "valueAttachment": {
       "contentType": "application/json",
       "data": "eyJoZWFkZXIiOi..." // Base64-encoded OMH data
     }
   }

Open mHealth Payload
~~~~~~~~~~~~~~~~~~~~

The decoded ``valueAttachment.data`` contains the complete OMH structure:

.. code-block:: json

   {
     "header": {
       "uuid": "abc-123",
       "schema_id": {
         "namespace": "omh",
         "name": "blood-glucose",
         "version": "4.0"
       },
       "source_creation_date_time": "2025-01-15T10:30:00Z",
       "source_data_point_id": "device-xyz-12345",
       "acquisition_provenance": {
         "source_name": "Dexcom G6",
         "source_origin_id": "serial:ABC123"
       }
     },
     "body": {
       "blood_glucose": {
         "value": 129,
         "unit": "mg/dL"
       },
       "effective_time_frame": {
         "date_time": "2025-01-15T10:30:00Z"
       },
       "specimen_source": "capillary_blood",
       "temporal_relationship_to_meal": "fasting"
     }
   }

Supported Health Metrics
------------------------

Currently validated Open mHealth schemas:

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Metric
     - Version
     - Supported Units/Fields
   * - **Blood Glucose**
     - 4.0
     - mg/dL, mmol/L, specimen source, meal relationship
   * - **Blood Pressure**
     - 4.0
     - systolic/diastolic mmHg, body position, cuff size
   * - **Body Temperature**
     - 4.0
     - °F, °C, measurement site (oral, tympanic, etc.)
   * - **Heart Rate**
     - 2.0
     - bpm, activity context, temporal relationship
   * - **Oxygen Saturation**
     - 2.0
     - SpO2 percentage, supplemental O2 flow rate
   * - **Respiratory Rate**
     - 2.0
     - breaths/minute, measurement method
   * - **RR Interval**
     - 1.0
     - milliseconds, heart rate variability metrics

Implementation Details
----------------------

Storage Strategy
~~~~~~~~~~~~~~~~

.. code-block:: sql

   -- PostgreSQL JSONB storage enables efficient queries
   CREATE TABLE observations (
     id UUID PRIMARY KEY,
     fhir_resource JSONB,  -- Complete FHIR Observation
     omh_data JSONB,       -- Decoded OMH payload (indexed)
     patient_id UUID,
     study_id UUID,
     created_at TIMESTAMP
   );

   -- Index for efficient OMH queries
   CREATE INDEX idx_omh_schema ON observations
     ((omh_data->'header'->'schema_id'->>'name'));
   CREATE INDEX idx_omh_value ON observations
     ((omh_data->'body'->'blood_glucose'->>'value'));

Query Examples
~~~~~~~~~~~~~~

**FHIR Query** (standard FHIR search):

.. code-block:: http

   GET /fhir/r5/Observation?
     patient=Patient/123&
     code=https://w3id.org/openmhealth|omh:blood-glucose:4.0&
     date=ge2025-01-01

**Direct Database Query** (for analytics):

.. code-block:: sql

   SELECT
     omh_data->'body'->'blood_glucose'->>'value' as glucose,
     omh_data->'body'->'effective_time_frame'->>'date_time' as measured_at
   FROM observations
   WHERE
     patient_id = '123' AND
     omh_data->'header'->'schema_id'->>'name' = 'blood-glucose' AND
     (omh_data->'body'->'blood_glucose'->>'value')::float > 180;

Validation Pipeline
-------------------

Data goes through a two-tier validation process:

.. code-block:: python

   # 1. Validate FHIR structure
   from fhir.resources.observation import Observation

   try:
       obs = Observation.parse_obj(fhir_data)
   except ValidationError as e:
       return {"error": "Invalid FHIR structure", "details": str(e)}

   # 2. Extract and validate OMH content
   import base64
   import json
   from jsonschema import validate

   omh_encoded = obs.valueAttachment.data
   omh_json = json.loads(base64.b64decode(omh_encoded))

   # Load appropriate OMH schema
   schema_name = omh_json['header']['schema_id']['name']
   schema_version = omh_json['header']['schema_id']['version']
   omh_schema = load_omh_schema(schema_name, schema_version)

   # Validate against OMH schema
   try:
       validate(instance=omh_json, schema=omh_schema)
   except ValidationError as e:
       return {"error": "Invalid OMH data", "details": str(e)}

API Response Example
--------------------

When querying observations, the API returns both FHIR-compliant responses and decoded OMH data:

.. code-block:: json

   {
     "resourceType": "Bundle",
     "type": "searchset",
     "entry": [{
       "resource": {
         "resourceType": "Observation",
         "id": "obs-123",
         "code": {
           "coding": [{
             "system": "https://w3id.org/openmhealth",
             "code": "omh:blood-glucose:4.0"
           }]
         },
         "valueAttachment": {
           "contentType": "application/json",
           "data": "eyJoZWFkZXIiOi..."
         },
         "extension": [{
           "url": "https://jupyterhealth.org/fhir/StructureDefinition/omh-decoded",
           "valueString": "{\"blood_glucose\":{\"value\":129,\"unit\":\"mg/dL\"}}"
         }]
       }
     }]
   }

Best Practices
--------------

1. **Always validate both layers** - FHIR structure and OMH content
2. **Preserve original timestamps** - Use device timestamps, not server time
3. **Include provenance** - Track device serial numbers and software versions
4. **Handle units consistently** - Convert to standard units during ingestion
5. **Index strategically** - Create database indexes for common query patterns

Adding New Device Types
-----------------------

To support a new device or metric:

1. **Create or obtain OMH schema**:

   .. code-block:: json

      {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
          "header": { "$ref": "#/definitions/header" },
          "body": {
            "type": "object",
            "properties": {
              "new_metric": {
                "type": "object",
                "properties": {
                  "value": { "type": "number" },
                  "unit": { "type": "string" }
                }
              }
            }
          }
        }
      }

2. **Register in JHE configuration**:

   .. code-block:: python

      OMH_SCHEMAS = {
          'new-metric': {
              'version': '1.0',
              'schema_file': 'schemas/omh/new-metric-1.0.json',
              'fhir_code': 'omh:new-metric:1.0'
          }
      }

3. **Create data transformer**:

   .. code-block:: python

      def transform_new_device(raw_data):
          return {
              'header': create_omh_header('new-metric', '1.0'),
              'body': {
                  'new_metric': {
                      'value': raw_data['reading'],
                      'unit': raw_data['unit']
                  }
              }
          }

4. **Test end-to-end**:
   - Validate sample data
   - Verify FHIR wrapper creation
   - Test query capabilities
   - Confirm visualization

Next Steps
----------

* :doc:`/api/fhir` - FHIR API documentation
* :doc:`/configuration/index` - Configure OMH schemas
* :doc:`/workflow-guide` - See integration in practice