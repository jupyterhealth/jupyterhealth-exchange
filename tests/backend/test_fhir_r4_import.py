"""Tests for R4 ingestion: the cross-version transform engine (core/fhir/cross_version) and the
/fhir-import/R4 endpoint (core/views/fhir_import.py).

The engine converts an R4 body to R5 by interpreting the bundled HL7 ``*4to5`` FML StructureMaps;
the endpoint then reuses the normal create routing (mapped-vs-aux, R5 validation, source header,
provenance).
"""

import json
import pathlib
import re
import uuid

import pytest

from core.fhir.cross_version import XVerError, dropped_field_paths, transform_to_r5
from core.fhir.fhir_validation import validate_fhir_resource
from core.models import FhirAuxResource, FhirSource, fhir_source_uri

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
    "Encounter": {"resourceType": "Encounter", "status": "finished"},
    "CarePlan": {"resourceType": "CarePlan", "status": "active", "intent": "plan", "subject": _PATIENT_REF},
    "Device": {"resourceType": "Device"},
}


@pytest.fixture
def fhir_source(patient, device):
    return FhirSource.objects.create(patient=patient, data_source=device, label="Patient EHR")


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


def test_engine_copy_transform_keeps_repeating_primitive_list():
    # AllergyIntolerance.category maps through a plain ``copy`` target (unlike Patient's
    # repeating primitives, which go through ``create``). The rule runs once per source item,
    # so a copy that assigns instead of appending keeps only the last item and mistypes the
    # element as a scalar -- every Epic allergy carries a category, so all of them failed R5
    # validation with "value is not a valid list".
    r4 = {
        "resourceType": "AllergyIntolerance",
        "category": ["medication", "food"],
        "code": {"text": "PENICILLIN G"},
        "patient": {"reference": "Patient/p1"},
    }
    out = transform_to_r5("AllergyIntolerance", r4)
    validate_fhir_resource("AllergyIntolerance", out)
    assert out["category"] == ["medication", "food"]


def test_engine_assign_semantics_directly():
    # _assign backs both the copy and translate transforms: repeating target -> append per
    # iterated source item; scalar target or already-a-list value -> plain assignment; a
    # scalar left under a repeating key by any earlier rule is promoted to a list.
    from fhir.resources.allergyintolerance import AllergyIntolerance as R5A

    from core.fhir.cross_version import _Engine, _Var

    ctx = _Var({}, R5A)
    _Engine._assign(ctx, "category", "medication")
    _Engine._assign(ctx, "category", "food")
    assert ctx.value["category"] == ["medication", "food"]

    ctx = _Var({}, R5A)
    _Engine._assign(ctx, "category", ["medication", "food"])  # pre-built list assigns wholesale
    assert ctx.value["category"] == ["medication", "food"]

    ctx = _Var({"category": "medication"}, R5A)  # scalar residue under a repeating key
    _Engine._assign(ctx, "category", "food")
    assert ctx.value["category"] == ["medication", "food"]

    ctx = _Var({}, R5A)
    _Engine._assign(ctx, "criticality", "high")  # scalar element: plain assignment
    assert ctx.value["criticality"] == "high"


def test_engine_translate_transform_appends_on_repeating_element():
    # translate shares _assign with copy, but no pack map translates onto a repeating
    # element today, so drive the engine with a minimal synthetic map: both source codes
    # must survive a translate-transform rule over a repeating element (plain assignment
    # would keep only the last and mistype the element as a scalar).
    from core.fhir.cross_version import _Engine

    group = {
        "name": "AllergyIntolerance",
        "input": [{"name": "src", "mode": "source"}, {"name": "tgt", "mode": "target"}],
        "rule": [
            {
                "name": "category",
                "source": [{"context": "src", "element": "category", "variable": "v", "type": "code"}],
                "target": [
                    {
                        "context": "tgt",
                        "element": "category",
                        "transform": "translate",
                        "parameter": [{"valueId": "v"}, {"valueString": "http://example.org/cat4to5"}],
                    }
                ],
            },
            {
                "name": "patient",  # R5-required; carried over so the output validates
                "source": [{"context": "src", "element": "patient", "variable": "p"}],
                "target": [
                    {"context": "tgt", "element": "patient", "transform": "copy", "parameter": [{"valueId": "p"}]}
                ],
            },
        ],
    }

    class _StubMaps:
        def group_for(self, name):
            return group if name == "AllergyIntolerance" else None

        def translate(self, url, code):
            assert url == "http://example.org/cat4to5"
            return {"med": "medication", "fd": "food"}[code]

    r4 = {"resourceType": "AllergyIntolerance", "category": ["med", "fd"], "patient": {"reference": "Patient/p1"}}
    out = _Engine(maps=_StubMaps()).transform("AllergyIntolerance", r4)
    validate_fhir_resource("AllergyIntolerance", out)
    assert out["category"] == ["medication", "food"]


def test_engine_copy_flattens_choice_target_and_wraps_codeableconcept():
    # Two DocumentReference4to5 gaps found via live Epic pulls (every clinical-note
    # document failed R5 validation):
    # 1. R4 ``content.format`` (Coding) copies into R5 ``content.profile.value[x]`` -- a
    #    choice element the copy transform must flatten to ``valueCoding``, exactly as
    #    ``create`` targets already do.
    # 2. R4 ``authenticator`` maps to an attester whose ``mode`` the map fills with the
    #    bare code "professional", but R5 mode is a CodeableConcept -- the copy wraps it.
    r4 = {
        "resourceType": "DocumentReference",
        "status": "current",
        "content": [
            {
                "attachment": {"contentType": "text/html", "url": "Binary/b1"},
                "format": {
                    "system": "http://ihe.net/fhir/ValueSet/IHE.FormatCode.codesystem",
                    "code": "urn:ihe:iti:xds:2017:mimeTypeSufficient",
                },
            }
        ],
        "authenticator": {"reference": "Practitioner/pr1"},
    }
    out = transform_to_r5("DocumentReference", r4)
    validate_fhir_resource("DocumentReference", out)
    profile = out["content"][0]["profile"][0]
    assert profile["valueCoding"]["code"] == "urn:ihe:iti:xds:2017:mimeTypeSufficient"
    # The local patch map merges the official map's two attester rules into one entry
    # carrying both mode and party (data/fhir/fhir-cross-version-patches).
    assert len(out["attester"]) == 1
    attester = out["attester"][0]
    assert attester["party"]["reference"] == "Practitioner/pr1"
    assert attester["mode"] == {"text": "professional"}


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


def _r4_condition(patient, resource_id, text="HTN"):
    return {
        "resourceType": "Condition",
        "id": resource_id,
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "code": {"text": text},
        "subject": {"reference": f"Patient/{patient.id}"},
    }


def test_reimport_of_the_same_record_overwrites_it(api_client, patient, fhir_source):
    # Reconnecting an EHR reuses the patient's source for that brand, so a re-import posts the same
    # record to the same source: the EHR's latest copy replaces the stored one, which keeps its JHE
    # id. The entry says 201 for the first import and 200 for the overwrite.
    first = api_client.post("/fhir-import/R4/Condition", _r4_condition(patient, "epic-cond-1"), **_src(fhir_source))
    again = api_client.post(
        "/fhir-import/R4/Condition", _r4_condition(patient, "epic-cond-1", "HTN (updated)"), **_src(fhir_source)
    )
    assert first.status_code == 200 and again.status_code == 200
    first_entry = first.json()["entry"][0]
    again_entry = again.json()["entry"][0]
    assert first_entry["response"]["status"] == "201 Created"
    assert again_entry["response"]["status"] == "200 OK", again.text
    assert again_entry["resource"]["id"] == first_entry["resource"]["id"]

    rows = FhirAuxResource.objects.filter(resource_type="Condition", fhir_resource_id="epic-cond-1")
    assert rows.count() == 1
    row = rows.get()
    assert str(row.pk) == first_entry["resource"]["id"]
    assert row.fhir_data["code"] == {"text": "HTN (updated)"}
    # The upstream id is stamped once, not once per import.
    stamp = {"system": fhir_source_uri(fhir_source.pk), "value": "epic-cond-1"}
    assert row.fhir_data["identifier"].count(stamp) == 1


def test_reimport_of_an_unchanged_record_is_not_saved_again(api_client, patient, fhir_source):
    # An identical re-import answers 200 but leaves the row alone, so last_updated only moves when
    # the record actually changed.
    api_client.post("/fhir-import/R4/Condition", _r4_condition(patient, "epic-cond-1"), **_src(fhir_source))
    row = FhirAuxResource.objects.get(resource_type="Condition", fhir_resource_id="epic-cond-1")
    FhirAuxResource.objects.filter(pk=row.pk).update(ref_indexed=True)  # a save would reset this

    again = api_client.post("/fhir-import/R4/Condition", _r4_condition(patient, "epic-cond-1"), **_src(fhir_source))
    assert again.json()["entry"][0]["response"]["status"] == "200 OK", again.text
    after = FhirAuxResource.objects.get(pk=row.pk)
    assert after.last_updated == row.last_updated
    assert after.ref_indexed is True


def test_reimport_bundle_reports_new_and_existing_records(api_client, patient, fhir_source):
    api_client.post("/fhir-import/R4/Condition", _r4_condition(patient, "epic-cond-1"), **_src(fhir_source))
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {"resource": _r4_condition(patient, "epic-cond-1", "HTN (updated)")},
            {"resource": _r4_condition(patient, "epic-cond-2")},
        ],
    }
    response = api_client.post("/fhir-import/R4/", bundle, **_src(fhir_source))
    statuses = [entry["response"]["status"] for entry in response.json()["entry"]]
    assert statuses == ["200 OK", "201 Created"], response.text
    assert FhirAuxResource.objects.filter(fhir_source=fhir_source, resource_type="Condition").count() == 2


def test_reimport_that_loses_a_race_overwrites_the_parallel_row(api_client, patient, fhir_source, monkeypatch):
    # Two imports of a new record at once: both find no row, and the second insert hits the
    # uniqueness constraint. That import retries as an overwrite of the row the first one created.
    from core.views import fhir as fhir_views

    real_create = fhir_views.create_aux_resource
    calls = []

    def create_after_a_parallel_import(resource_type, data, source, upstream_id=None):
        if not calls:
            # The parallel import commits its row between this import's lookup and its insert.
            real_create(resource_type, dict(data, code={"text": "parallel"}), source, upstream_id=upstream_id)
        calls.append(upstream_id)
        return real_create(resource_type, data, source, upstream_id=upstream_id)

    monkeypatch.setattr(fhir_views, "create_aux_resource", create_after_a_parallel_import)
    response = api_client.post(
        "/fhir-import/R4/Condition", _r4_condition(patient, "epic-cond-1", "mine"), **_src(fhir_source)
    )
    assert response.json()["entry"][0]["response"]["status"] == "200 OK", response.text
    row = FhirAuxResource.objects.get(resource_type="Condition", fhir_resource_id="epic-cond-1")
    assert row.fhir_data["code"] == {"text": "mine"}


def test_reimport_under_a_different_source_creates_a_second_record(api_client, patient, device, fhir_source):
    # Each source is its own identifier namespace, so the same upstream id under a second source
    # is a second record, not an overwrite of the first source's copy.
    other = FhirSource.objects.create(patient=patient, data_source=device, label="Second connect")
    r4 = _r4_condition(patient, "epic-cond-1")
    first = api_client.post("/fhir-import/R4/Condition", r4, **_src(fhir_source))
    second = api_client.post("/fhir-import/R4/Condition", r4, **_src(other))
    assert all(r.json()["entry"][0]["response"]["status"] == "201 Created" for r in (first, second))
    assert FhirAuxResource.objects.filter(resource_type="Condition", fhir_resource_id="epic-cond-1").count() == 2


def test_import_long_epic_id_lands_in_an_identifier(api_client, patient, fhir_source):
    # Epic "Unconstrained FHIR IDs" exceed the 64-char id limit; the server moves the id into an
    # identifier namespaced by the source, so the record imports and stays unique within it.
    long_id = "e" + "Q" * 80
    r4 = _r4_condition(patient, long_id)
    first = api_client.post("/fhir-import/R4/Condition", r4, **_src(fhir_source))
    assert first.json()["entry"][0]["response"]["status"].startswith("2"), first.text
    created = first.json()["entry"][0]["resource"]
    assert {"system": fhir_source_uri(fhir_source.pk), "value": long_id} in created["identifier"]

    # A re-import matches on the long id too, and overwrites rather than adding a second row.
    again = api_client.post("/fhir-import/R4/Condition", r4, **_src(fhir_source))
    assert again.json()["entry"][0]["response"]["status"] == "200 OK", again.text
    assert FhirAuxResource.objects.filter(resource_type="Condition", fhir_resource_id=long_id).count() == 1


def test_import_bundle_malformed_entry_fails_alone(api_client, patient, fhir_source):
    # A malformed entry gets ITS error entry; the valid neighbours still import (per-entry
    # best-effort — earlier entries are already committed, so a whole-request error would lie).
    good = {
        "resourceType": "Condition",
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "subject": {"reference": f"Patient/{patient.id}"},
    }
    bundle = {"resourceType": "Bundle", "type": "batch", "entry": [{"resource": good}, {"resource": "oops"}, "junk"]}
    r = api_client.post("/fhir-import/R4", bundle, **_src(fhir_source))
    assert r.status_code == 200, r.text
    statuses = [e["response"]["status"] for e in r.json()["entry"]]
    assert statuses[0].startswith("2")
    assert not statuses[1].startswith("2") and not statuses[2].startswith("2")


def test_import_bundle_honors_per_entry_meta_source(api_client, patient, fhir_source):
    # No header: each entry names its source via meta.source — the request-level gate must
    # defer instead of 400ing with advice the bundle route then refuses to honor.
    from core.models import fhir_source_uri

    body = {
        "resourceType": "Condition",
        "meta": {"source": fhir_source_uri(fhir_source.id)},
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "subject": {"reference": f"Patient/{patient.id}"},
    }
    r = api_client.post("/fhir-import/R4", {"resourceType": "Bundle", "type": "batch", "entry": [{"resource": body}]})
    assert r.status_code == 200, r.text
    assert r.json()["entry"][0]["response"]["status"].startswith("2")
    # Nothing names a source anywhere -> still a request-level 400.
    r = api_client.post("/fhir-import/R4", {"resourceType": "Bundle", "type": "batch", "entry": [{"resource": {}}]})
    assert r.status_code == 400


def test_import_device_issuerless_udicarrier_is_dropped_with_warning(api_client, patient, fhir_source):
    # R4 udiCarrier.issuer is 0..1, R5 requires it; Epic emits carriers without one. The
    # incomplete carrier is dropped (warned per record) instead of rejecting the Device.
    r4 = {
        "resourceType": "Device",
        "udiCarrier": [{"deviceIdentifier": "00844588003288", "carrierHRF": "(01)00844588003288"}],
        "patient": {"reference": f"Patient/{patient.id}"},
    }
    r = api_client.post("/fhir-import/R4/Device", r4, **_src(fhir_source))
    assert r.status_code == 200, r.text
    entry = r.json()["entry"][0]
    assert entry["response"]["status"].startswith("2")
    assert "udiCarrier" not in entry["resource"]
    warnings = [i for i in entry["response"]["outcome"]["issue"] if i["severity"] == "warning"]
    assert any("udiCarrier" in (i.get("diagnostics") or "") for i in warnings)


def test_import_error_entry_still_reports_enrichment(api_client, fhir_source):
    # A Condition with no clinicalStatus is enriched (defaulted to 'unknown') before the create;
    # when the create then fails (no subject here), the enrichment warning must survive into the
    # error entry — it narrows what was actually attempted.
    r4 = {"resourceType": "Condition", "code": {"text": "HTN"}}
    r = api_client.post("/fhir-import/R4/Condition", r4, **_src(fhir_source))
    assert r.status_code == 200, r.text
    issues = r.json()["entry"][0]["response"]["outcome"]["issue"]
    assert any(i["severity"] == "error" for i in issues)
    assert any(i["severity"] == "warning" and i.get("expression") == ["Condition.clinicalStatus"] for i in issues)


def test_import_error_issue_carries_diagnostics_string(api_client, fhir_source):
    # The EHR Patient Portal client renders entry.response.outcome.issue[].diagnostics for failed
    # records (paEntryFailureReason). Pin the contract: the first error issue is severity
    # "error" with a non-empty diagnostics string.
    # Coverage reliably fails R5 validation (kind is new-in-R5 and mandatory; no map rule).
    r4 = {"resourceType": "Coverage", "status": "active", "beneficiary": {"reference": "Patient/p1"}}
    r = api_client.post("/fhir-import/R4/Coverage", r4, **_src(fhir_source))
    assert r.status_code == 200, r.text
    issue = r.json()["entry"][0]["response"]["outcome"]["issue"][0]
    assert issue["severity"] == "error"
    assert isinstance(issue["diagnostics"], str) and issue["diagnostics"]


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


# ---------------------------------------------------------------------------
# Pull-list invariants: everything the EHR Patient Portal client pulls must convert
# to valid R5 (with import-path enrichment applied), and every pulled type must
# have a matching entry in EhrBrand.SUPPORTED_SCOPES. These are the invariants RFC 0003 states.
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CLIENT_JS = _REPO_ROOT / "core" / "static" / "clients" / "ehr-patient-portal" / "js" / "client-ehr-patient-portal.js"


def _pulled_types():
    text = _CLIENT_JS.read_text()
    match = re.search(r"const EHR_PATIENT_PORTAL_PULLS = \[(.*?)\n\];", text, re.DOTALL)
    assert match, "EHR_PATIENT_PORTAL_PULLS not found in client-ehr-patient-portal.js — was it renamed or reformatted?"
    return set(re.findall(r'type: "(\w+)"', match.group(1)))


# Minimal Epic-shaped R4 instance per pulled type (Patient/Condition/Observation etc. are
# covered by dedicated tests above; this sweep guards the whole list against a type that
# cannot survive convert+validate — the failure mode that would have shipped Coverage).
_MINIMAL_PULL_R4 = {
    "Patient": {"resourceType": "Patient", "name": [{"family": "Doe"}]},
    "Condition": {"resourceType": "Condition", "subject": _PATIENT_REF},
    "MedicationRequest": {
        "resourceType": "MedicationRequest",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"text": "med"},
        "subject": _PATIENT_REF,
    },
    "MedicationDispense": {
        "resourceType": "MedicationDispense",
        "status": "completed",
        "medicationCodeableConcept": {"text": "med"},
        "subject": _PATIENT_REF,
    },
    "AllergyIntolerance": {"resourceType": "AllergyIntolerance", "category": ["medication"], "patient": _PATIENT_REF},
    "Immunization": _MINIMAL_R4["Immunization"],
    "Procedure": _MINIMAL_R4["Procedure"],
    "Observation": {
        "resourceType": "Observation",
        "status": "final",
        "category": [{"coding": [{"code": "laboratory"}]}],
        "code": {"text": "glucose"},
        "subject": _PATIENT_REF,
    },
    "DiagnosticReport": {
        "resourceType": "DiagnosticReport",
        "status": "final",
        "code": {"text": "panel"},
        "subject": _PATIENT_REF,
    },
    "DocumentReference": {
        "resourceType": "DocumentReference",
        "status": "current",
        "content": [{"attachment": {"contentType": "text/plain", "url": "Binary/b1"}}],
        "subject": _PATIENT_REF,
    },
    "Encounter": _MINIMAL_R4["Encounter"],
    "CarePlan": _MINIMAL_R4["CarePlan"],
    "CareTeam": {"resourceType": "CareTeam", "status": "active", "subject": _PATIENT_REF},
    "Goal": _MINIMAL_R4["Goal"],
    "ServiceRequest": _MINIMAL_R4["ServiceRequest"],
    "Device": _MINIMAL_R4["Device"],
    # R5 requires `questionnaire` (optional in R4); Epic populates it in practice, so the
    # fixture mirrors that - a rare source record without one fails with a labeled reason.
    "QuestionnaireResponse": {
        "resourceType": "QuestionnaireResponse",
        "status": "completed",
        "questionnaire": "Questionnaire/q1",
    },
}


def test_pull_list_fixture_coverage_matches_client():
    assert _pulled_types() == set(_MINIMAL_PULL_R4), (
        "EHR_PATIENT_PORTAL_PULLS and _MINIMAL_PULL_R4 diverged - add/remove the fixture "
        "so every pulled type stays convert+validate guarded"
    )


@pytest.mark.parametrize("resource_type", sorted(_MINIMAL_PULL_R4))
def test_every_pulled_type_converts_to_valid_r5(resource_type):
    from core.views.fhir_import import _enrich_r5

    r5 = transform_to_r5(resource_type, _MINIMAL_PULL_R4[resource_type])
    _enrich_r5(resource_type, r5)
    validate_fhir_resource(resource_type, r5)


def test_pulled_types_match_supported_scopes_catalog():
    from core.models import EhrVendor

    scoped = {token.removeprefix("patient/").removesuffix(".read") for token in EhrVendor.SUPPORTED_SCOPES}
    assert scoped == _pulled_types(), "EhrVendor.SUPPORTED_SCOPES and EHR_PATIENT_PORTAL_PULLS diverged"


def test_scope_migration_still_applies_to_a_historical_deployed_row(db):
    # Migration 0043 widened the scopes on already-deployed rows, back when scopes lived on
    # JheClient.aux_data (moved to EhrBrand.supported_scopes since). It is frozen history --
    # the only thing that fixes a database still sitting behind it, and `migrate` is all a
    # deploy runs -- so it is never touched, even though nothing in the live app reads
    # JheClient.aux_data["scopes"] anymore.
    #
    # It filters on the *old* client name — it ran before 0046 renamed the row to
    # "EHR Patient Portal", and applied migrations are frozen history. The row created below
    # is named "Patient Access" for that reason, and must not be "tidied up".
    import importlib

    from django.apps import apps as django_apps
    from oauth2_provider.models import get_application_model

    from core.models import JheClient

    migration = importlib.import_module("core.migrations.0043_patient_access_scopes")

    app = get_application_model().objects.create(name="Patient Access", client_id="local-app-id")
    JheClient.objects.create(
        application=app, aux_data={"client_id": "deployed-epic-id", "scopes": migration.OLD_SCOPES}
    )
    migration.widen_scopes(django_apps, None)
    aux = JheClient.objects.get(application=app).aux_data
    assert aux["scopes"] == migration.NEW_SCOPES
    assert aux["client_id"] == "deployed-epic-id"


def test_seed_fills_ehr_vendor_client_config_without_clobbering_operator_value(db):
    # ehr_client_id/supported_scopes are operator-editable (via the EHRs admin page) once a
    # vendor exists, so re-seeding must fill them in only when blank -- never overwrite an
    # operator's edit, the same fill-gap contract the old aux_data client_id had.
    from core.management.commands.seed import Command
    from core.models import EhrVendor

    vendor = EhrVendor.objects.create(name="Epic Sandbox")

    Command.seed_ehr_vendor_sandbox_config()
    vendor.refresh_from_db()
    assert vendor.ehr_client_id
    assert set(vendor.supported_scopes.split()) == set(EhrVendor.SUPPORTED_SCOPES)

    vendor.ehr_client_id = "operator-set-id"
    vendor.save()
    Command.seed_ehr_vendor_sandbox_config()
    vendor.refresh_from_db()
    assert vendor.ehr_client_id == "operator-set-id", "reseeding clobbered an operator-set client id"


def test_seed_links_the_portal_client_to_its_data_source(db):
    # The connect page reads the data source id off this link. Seeding it is what replaced the
    # view's request-time name lookup, so a missing link is a broken connect page.
    from oauth2_provider.models import get_application_model

    from core.management.commands.seed import Command
    from core.models import ClientDataSource, DataSource

    command = Command()
    command.seed_codeable_concepts()
    command.seed_data_sources()
    command.seed_clients()

    app = get_application_model().objects.get(name="EHR Patient Portal")
    data_source = DataSource.objects.get(name="EHR Patient Portal")
    assert ClientDataSource.objects.filter(client=app, data_source=data_source).exists()
    # One product, one name -- and not a device.
    assert data_source.type == "patient_app"


def test_seed_data_sources_retype_updates_rather_than_duplicates(db):
    # update_or_create matched on (name, type) before, so changing a type in the seed list
    # created a second row and orphaned the FKs pointing at the original.
    from core.management.commands.seed import Command
    from core.models import DataSource

    stale = DataSource.objects.create(name="EHR Patient Portal", type="medical_device")

    command = Command()
    command.seed_codeable_concepts()
    command.seed_data_sources()

    assert DataSource.objects.filter(name="EHR Patient Portal").count() == 1
    stale.refresh_from_db()
    assert stale.type == "patient_app"


def test_import_condition_without_clinical_status_defaults_to_unknown(api_client, patient, fhir_source):
    # R4 Condition.clinicalStatus is optional; R5 requires it. The import defaults absent
    # statuses to the value set's 'unknown' (per product decision) and says so per record.
    r4 = {"resourceType": "Condition", "code": {"text": "HTN"}, "subject": {"reference": f"Patient/{patient.id}"}}
    r = api_client.post("/fhir-import/R4/Condition", r4, **_src(fhir_source))
    assert r.status_code == 200, r.text
    entry = r.json()["entry"][0]
    assert entry["response"]["status"].startswith("2")
    assert entry["resource"]["clinicalStatus"]["coding"][0]["code"] == "unknown"
    warnings = [i for i in entry["response"]["outcome"]["issue"] if i["severity"] == "warning"]
    assert any("clinicalStatus" in (i.get("diagnostics") or "") for i in warnings)


def test_import_condition_with_clinical_status_is_untouched(api_client, patient, fhir_source):
    r4 = {
        "resourceType": "Condition",
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "code": {"text": "HTN"},
        "subject": {"reference": f"Patient/{patient.id}"},
    }
    r = api_client.post("/fhir-import/R4/Condition", r4, **_src(fhir_source))
    entry = r.json()["entry"][0]
    assert entry["response"]["status"].startswith("2")
    assert entry["resource"]["clinicalStatus"]["coding"][0]["code"] == "active"
