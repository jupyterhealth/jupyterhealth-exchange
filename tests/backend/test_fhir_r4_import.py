"""Tests for R4 ingestion: the cross-version transform engine (core/fhir/cross_version) and the
/fhir-import/R4 endpoint (core/views/fhir_import.py).

The engine converts an R4 body to R5 by interpreting the bundled HL7 ``*4to5`` FML StructureMaps;
the endpoint then reuses the normal create routing (mapped-vs-aux, R5 validation, source header,
provenance).
"""

import json
import uuid

import pytest

from core.fhir.cross_version import XVerError, dropped_field_paths, transform_to_r5
from core.fhir.fhir_validation import validate_fhir_resource
from core.models import FhirAuxResource, FhirSource

# Smallest R4 body that still validates as R5 for each type, so a CodeableReference case can add
# just the element under test.
_PATIENT_REF = {"reference": "Patient/p1"}
_MINIMAL_R4 = {
    "Procedure": {"resourceType": "Procedure", "status": "completed", "subject": _PATIENT_REF},
    "ServiceRequest": {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "subject": _PATIENT_REF,
    },
    "Goal": {
        "resourceType": "Goal",
        "lifecycleStatus": "active",
        "description": {"text": "g"},
        "subject": _PATIENT_REF,
    },
    "Immunization": {
        "resourceType": "Immunization",
        "status": "completed",
        "vaccineCode": {"text": "v"},
        "patient": _PATIENT_REF,
        "occurrenceDateTime": "2024-01-01",
    },
    "FamilyMemberHistory": {
        "resourceType": "FamilyMemberHistory",
        "status": "completed",
        "patient": _PATIENT_REF,
        "relationship": {"text": "mother"},
    },
    "Encounter": {"resourceType": "Encounter", "status": "finished"},
    "CarePlan": {"resourceType": "CarePlan", "status": "active", "intent": "plan", "subject": _PATIENT_REF},
    "Device": {"resourceType": "Device"},
}


@pytest.fixture
def fhir_source(patient, device):
    return FhirSource.objects.create(
        patient=patient, data_source=device, label="Patient EHR", fhir_base_url="https://ehr.example/fhir"
    )


def _src(fhir_source):
    return {"HTTP_X_JHE_FHIR_SOURCE_ID": str(fhir_source.id)}


# ---------------------------------------------------------------------------
# Engine (no DB): shape correctness against real HL7 maps
# ---------------------------------------------------------------------------


def test_engine_observation_choice_and_backbone():
    r4 = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9"}]},
        "subject": {"reference": "Patient/p1"},
        "effectiveDateTime": "2023-01-15T10:30:00Z",  # choice element value[x]/effective[x]
        "valueQuantity": {"value": 72, "unit": "mmHg", "system": "http://unitsofmeasure.org", "code": "mm[Hg]"},
        "component": [  # repeating backbone element
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
                "valueQuantity": {"value": 120, "system": "http://unitsofmeasure.org", "code": "mm[Hg]"},
            }
        ],
    }
    out = transform_to_r5("Observation", r4)
    validate_fhir_resource("Observation", out)  # raises on invalid R5

    assert out["resourceType"] == "Observation"
    # choice element preserved under its flattened key
    assert out["effectiveDateTime"] == "2023-01-15T10:30:00Z"
    assert out["valueQuantity"]["value"] == 72
    # backbone element (and its own nested choice) preserved
    assert out["component"][0]["code"]["coding"][0]["code"] == "8480-6"
    assert out["component"][0]["valueQuantity"]["value"] == 120


def test_engine_patient_repeating_primitives():
    r4 = {
        "resourceType": "Patient",
        "active": True,
        "name": [{"use": "official", "family": "Doe", "given": ["Jane", "Q"]}],
        "gender": "female",
        "birthDate": "1990-05-05",
        "address": [{"line": ["1 Main St", "Apt 2"], "city": "Boston"}],
    }
    out = transform_to_r5("Patient", r4)
    validate_fhir_resource("Patient", out)

    # repeating *primitive* elements must stay lists, not collapse to a scalar
    assert out["name"][0]["given"] == ["Jane", "Q"]
    assert out["name"][0]["family"] == "Doe"
    assert out["address"][0]["line"] == ["1 Main St", "Apt 2"]


def test_engine_condition_valid_r5():
    r4 = {
        "resourceType": "Condition",
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "44054006"}], "text": "Diabetes"},
        "subject": {"reference": "Patient/p1"},
        "onsetDateTime": "2020-01-01",
    }
    out = transform_to_r5("Condition", r4)
    validate_fhir_resource("Condition", out)
    assert out["code"]["text"] == "Diabetes"
    assert out["onsetDateTime"] == "2020-01-01"


def test_engine_unknown_resource_raises():
    with pytest.raises(XVerError):
        transform_to_r5("NotAResource", {"resourceType": "NotAResource"})


def test_engine_medicationrequest_codeablereference_and_patch():
    # R4 medication[x] / reasonCode / reasonReference all became R5 CodeableReference. Exercises R4
    # source typing and (source, target) pair dispatch into the HL7 conversion groups -- all from
    # the stock maps, with no local patch.
    r4 = {
        "resourceType": "MedicationRequest",
        "status": "active",
        "intent": "order",
        "medicationReference": {"reference": "Medication/x"},
        "subject": {"reference": "Patient/p1"},
        "reasonCode": [{"text": "headache"}],
        "reasonReference": [{"reference": "Condition/c1"}],
        "dosageInstruction": [{"text": "1 tab daily"}],
    }
    out = transform_to_r5("MedicationRequest", r4)
    validate_fhir_resource("MedicationRequest", out)  # raises on invalid R5 -- was the blocker

    # medicationReference -> medication.reference (a nested Reference), not a flat Reference
    assert out["medication"] == {"reference": {"reference": "Medication/x"}}
    # reasonCode -> reason[].concept ; reasonReference -> reason[].reference
    assert {"concept": {"text": "headache"}} in out["reason"]
    assert {"reference": {"reference": "Condition/c1"}} in out["reason"]
    assert out["dosageInstruction"][0]["text"] == "1 tab daily"


def test_engine_reason_carries_coding_not_just_text():
    # Typing the source from R4 types its *children* too. When sources were typed from R5 the
    # untypedness was contagious and only the primitive `.text` survived -- `.coding` was dropped.
    r4 = {
        "resourceType": "MedicationRequest",
        "status": "active",
        "intent": "order",
        "subject": {"reference": "Patient/p1"},
        "medicationReference": {"reference": "Medication/x"},  # required in R5
        "reasonCode": [{"text": "headache", "coding": [{"system": "http://snomed.info/sct", "code": "25064002"}]}],
    }
    out = transform_to_r5("MedicationRequest", r4)
    validate_fhir_resource("MedicationRequest", out)
    concept = out["reason"][0]["concept"]
    assert concept["coding"] == [{"system": "http://snomed.info/sct", "code": "25064002"}]
    assert concept["text"] == "headache"


def test_engine_implicit_datatype_dispatch():
    # The current pack emits no DefaultMappingGroupAnonymousAlias dependent: a bare
    # `src.x -> tgt.x` implies datatype dispatch, which the engine synthesises. Without that, every
    # complex element would convert to an empty object and be pruned away.
    r4 = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9"}]},
        "component": [
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
                "valueQuantity": {"value": 120, "unit": "mmHg"},
                # referenceRange on a *component* gained a rule in the 2026-03 pack
                "referenceRange": [{"low": {"value": 90, "unit": "mmHg"}}],
            }
        ],
    }
    out = transform_to_r5("Observation", r4)
    validate_fhir_resource("Observation", out)
    component = out["component"][0]
    assert component["code"]["coding"] == [{"system": "http://loinc.org", "code": "8480-6"}]
    assert component["referenceRange"][0]["low"] == {"value": 90, "unit": "mmHg"}


def test_engine_parenthesised_condition():
    # Newer maps parenthesise rule conditions (`where (s = 'allergy')`); AllergyIntolerance.type is
    # mapped only through such a rule, so a parser that cannot read them drops the field.
    r4 = {"resourceType": "AllergyIntolerance", "type": "allergy", "patient": {"reference": "Patient/p1"}}
    out = transform_to_r5("AllergyIntolerance", r4)
    validate_fhir_resource("AllergyIntolerance", out)
    assert out["type"]["coding"][0]["code"] == "allergy"


def test_engine_medication_codeableconcept_nests_under_concept():
    r4 = {
        "resourceType": "MedicationRequest",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"coding": [{"system": "http://snomed.info/sct", "code": "111"}]},
        "subject": {"reference": "Patient/p1"},
    }
    out = transform_to_r5("MedicationRequest", r4)
    validate_fhir_resource("MedicationRequest", out)
    assert out["medication"] == {"concept": {"coding": [{"system": "http://snomed.info/sct", "code": "111"}]}}


@pytest.mark.parametrize(
    "resource, r4_extra, element, expected",
    [
        # R4 pairs that R5 merged into one CodeableReference: the CodeableConcept arm nests under
        # .concept, the Reference arm under .reference. None of these rules is typed in the map --
        # the engine gets the source type from the R4 model, as a conforming FML engine must.
        ("Procedure", {"reasonCode": [{"text": "r"}]}, "reason", [{"concept": {"text": "r"}}]),
        (
            "Procedure",
            {"usedReference": [{"reference": "Device/d"}]},
            "used",
            [{"reference": {"reference": "Device/d"}}],
        ),
        ("ServiceRequest", {"reasonCode": [{"text": "r"}]}, "reason", [{"concept": {"text": "r"}}]),
        ("ServiceRequest", {"locationCode": [{"text": "l"}]}, "location", [{"concept": {"text": "l"}}]),
        ("Goal", {"outcomeCode": [{"text": "o"}]}, "outcome", [{"concept": {"text": "o"}}]),
        ("Immunization", {"reasonCode": [{"text": "r"}]}, "reason", [{"concept": {"text": "r"}}]),
        ("FamilyMemberHistory", {"reasonCode": [{"text": "r"}]}, "reason", [{"concept": {"text": "r"}}]),
        # ...and standalone elements R5 simply retyped. These kept their *name*, which is what makes
        # them the sharp case: typing the source from R5 "succeeds" with the wrong (R5) type.
        ("Encounter", {"serviceType": {"text": "s"}}, "serviceType", [{"concept": {"text": "s"}}]),
        (
            "CarePlan",
            {"addresses": [{"reference": "Condition/c"}]},
            "addresses",
            [{"reference": {"reference": "Condition/c"}}],
        ),
        (
            "Device",
            {"definition": {"reference": "DeviceDefinition/d"}},
            "definition",
            {"reference": {"reference": "DeviceDefinition/d"}},
        ),
    ],
)
def test_codeablereference_converts_from_stock_maps(resource, r4_extra, element, expected):
    r4 = {**_MINIMAL_R4[resource], **r4_extra}
    out = transform_to_r5(resource, r4)
    validate_fhir_resource(resource, out)
    assert out.get(element) == expected


def test_patch_mechanism_merges_rules_over_official_map(tmp_path, monkeypatch):
    # No patches ship any more (the R4 type index made them unnecessary), but the merge mechanism
    # remains for future upstream gaps, so keep it covered: a patch rule sharing an official rule's
    # name must REPLACE it, not append a second rule that would emit the element twice.
    from django.test import override_settings

    from core.fhir import cross_version_maps

    patch = {
        "resourceType": "StructureMap",
        "group": [
            {
                "name": "Observation",
                "rule": [
                    {
                        "name": "code",  # an official rule name -> override
                        "source": [{"context": "src", "element": "code", "variable": "vvv"}],
                        "target": [{"context": "tgt", "element": "bodyStructure", "variable": "vvv"}],
                    }
                ],
            }
        ],
    }
    (tmp_path / "StructureMap-Test-patch.json").write_text(json.dumps(patch))

    cross_version_maps.get_maps.cache_clear()
    try:
        with override_settings(FHIR_XVER_PATCHES_DIR=str(tmp_path)):
            group = cross_version_maps.get_maps().group_for("Observation")
            code_rules = [r for r in group["rule"] if r["name"] == "code"]
            assert len(code_rules) == 1  # replaced, not appended
            assert code_rules[0]["target"][0]["element"] == "bodyStructure"
    finally:
        cross_version_maps.get_maps.cache_clear()


# ---------------------------------------------------------------------------
# Dropped-field detection (leaf-value diff)
# ---------------------------------------------------------------------------


def test_dropped_field_paths_flags_true_loss_not_renames():
    r4 = {"resourceType": "X", "id": "1", "kept": "a", "moved": "b", "gone": "c"}
    r5 = {"resourceType": "X", "id": "1", "kept": "a", "renamed": "b"}  # 'moved' value survives as 'renamed'
    dropped = dropped_field_paths(r4, r5)
    assert dropped == ["gone"]  # only genuine loss; the rename (value 'b' preserved) is not flagged


def test_dropped_field_paths_collapses_dropped_subtree():
    r4 = {"resourceType": "X", "activity": [{"detail": {"status": "in-progress", "code": {"text": "walk"}}}]}
    r5 = {"resourceType": "X"}
    # the whole subtree is gone -> reported once at its root, not per leaf
    assert dropped_field_paths(r4, r5) == ["activity"]


# ---------------------------------------------------------------------------
# Endpoint: /fhir-import/R4 (single + Bundle), reusing the normal create path
# ---------------------------------------------------------------------------


def test_import_single_returns_bundle_and_stores_aux(api_client, patient, fhir_source):
    r4 = {
        "resourceType": "Condition",
        "id": "cond-r4",
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "code": {"text": "Hypertension"},
        "subject": {"reference": f"Patient/{patient.id}"},
    }
    r = api_client.post("/fhir-import/R4/Condition", r4, **_src(fhir_source))
    # Import always returns a batch-response Bundle, even for a single resource.
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "batch-response"
    entry = bundle["entry"][0]
    assert entry["response"]["status"] == "201 Created"
    # A clean conversion carries an informational "no loss" outcome.
    assert entry["response"]["outcome"]["issue"][0]["severity"] == "information"
    created = entry["resource"]
    assert created["resourceType"] == "Condition"
    assert created["code"] == {"text": "Hypertension"}

    # Stored as an aux row (UUID id) linked to the source and its patient, with provenance.
    assert uuid.UUID(created["id"])
    aux = FhirAuxResource.objects.get(pk=created["id"])
    assert aux.resource_type == "Condition"
    assert aux.fhir_source_id == fhir_source.id
    assert aux.fhir_resource_id == "cond-r4"  # upstream id preserved
    assert aux.patient_fhir_id == str(patient.id)


def test_import_reports_dropped_fields_as_warnings(api_client, patient, fhir_source):
    # R4 CarePlan.activity.detail was removed in R5, so it is dropped -- but the CarePlan is still
    # valid R5 and created, with a warning naming the dropped path.
    r4 = {
        "resourceType": "CarePlan",
        "status": "active",
        "intent": "plan",
        "subject": {"reference": f"Patient/{patient.id}"},
        "activity": [{"detail": {"status": "in-progress", "description": "Walk 30 min daily"}}],
    }
    r = api_client.post("/fhir-import/R4/CarePlan", r4, **_src(fhir_source))
    assert r.status_code == 200, r.text
    entry = r.json()["entry"][0]
    assert entry["response"]["status"] == "201 Created"  # still created
    issues = entry["response"]["outcome"]["issue"]
    assert all(i["severity"] == "warning" for i in issues)
    assert any(i["expression"] == ["CarePlan.activity"] for i in issues)
    # The lost data really is absent from the stored resource.
    assert "activity" not in entry["resource"]


def test_import_requires_source_header(api_client, patient):
    r4 = {
        "resourceType": "Condition",
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "subject": {"reference": f"Patient/{patient.id}"},
    }
    # Missing X-JHE-FHIR-Source-ID gates the whole request -> a real 400, not a per-entry outcome.
    assert api_client.post("/fhir-import/R4/Condition", r4).status_code == 400


def test_import_unsupported_resource_reports_error_entry(api_client, fhir_source):
    r = api_client.post("/fhir-import/R4/NotAResource", {"resourceType": "NotAResource"}, **_src(fhir_source))
    assert r.status_code == 200, r.text
    entry = r.json()["entry"][0]
    assert entry["response"]["status"].startswith("404")
    assert entry["response"]["outcome"]["issue"][0]["severity"] == "error"
    assert "resource" not in entry


def test_import_error_entry_still_reports_dropped_fields(api_client, patient, fhir_source):
    # R4 Coverage.payor has no R5 home (dropped) and its loss makes the R5 Coverage invalid. The
    # error entry must carry both the error AND the drop warning, so the cause is discoverable.
    r4 = {
        "resourceType": "Coverage",
        "status": "active",
        "beneficiary": {"reference": f"Patient/{patient.id}"},
        "payor": [{"reference": "Organization/1"}],
    }
    r = api_client.post("/fhir-import/R4/Coverage", r4, **_src(fhir_source))
    assert r.status_code == 200, r.text
    issues = r.json()["entry"][0]["response"]["outcome"]["issue"]
    severities = [i["severity"] for i in issues]
    assert "error" in severities
    assert any(i["severity"] == "warning" and i["expression"] == ["Coverage.payor"] for i in issues)


def test_import_get_not_allowed(api_client, fhir_source):
    assert api_client.get("/fhir-import/R4/Condition", **_src(fhir_source)).status_code == 405


def test_import_bundle_batch_response(api_client, patient, fhir_source):
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "resource": {
                    "resourceType": "Condition",
                    "clinicalStatus": {
                        "coding": [
                            {"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}
                        ]
                    },
                    "code": {"text": "Asthma"},
                    "subject": {"reference": f"Patient/{patient.id}"},
                }
            },
            {
                "resource": {
                    "resourceType": "Observation",
                    "status": "final",
                    "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
                    "subject": {"reference": f"Patient/{patient.id}"},
                    "valueQuantity": {"value": 80, "unit": "beats/min"},
                }
            },
        ],
    }
    r = api_client.post("/fhir-import/R4", bundle, **_src(fhir_source))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "batch-response"
    statuses = [e["response"]["status"] for e in body["entry"]]
    assert statuses == ["201 Created", "201 Created"]
    # Every entry carries an OperationOutcome at response.outcome.
    assert all(e["response"]["outcome"]["resourceType"] == "OperationOutcome" for e in body["entry"])
    # Both landed in aux (non-OMH Observation falls through to aux, like the normal endpoint).
    assert FhirAuxResource.objects.filter(fhir_source=fhir_source).count() == 2


def test_import_bundle_reports_per_entry_error(api_client, patient, fhir_source):
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {"resource": {"resourceType": "NotAResource"}},  # unsupported -> error entry
            {
                "resource": {
                    "resourceType": "Condition",
                    "clinicalStatus": {
                        "coding": [
                            {"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}
                        ]
                    },
                    "subject": {"reference": f"Patient/{patient.id}"},
                }
            },
        ],
    }
    r = api_client.post("/fhir-import/R4", bundle, **_src(fhir_source))
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert entries[0]["response"]["outcome"]["resourceType"] == "OperationOutcome"
    assert entries[1]["response"]["status"] == "201 Created"
