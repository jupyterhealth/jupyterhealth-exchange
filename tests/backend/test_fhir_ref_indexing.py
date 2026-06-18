"""Issue #584: the index-refs pass rewrites aux-resource references from upstream ids to
JHE ids (same-FhirSource lookup), marks rows ref_indexed, and is exposed as a FhirSource
detail endpoint. Also covers the create-time id rewrite (upstream id -> JHE UUID)."""

import pytest
from rest_framework.test import APIClient

from core.fhir.ref_indexing import index_fhir_source_refs
from core.models import FhirAuxResource, FhirSource
from core.views.fhir import create_aux_resource


@pytest.fixture
def fhir_source(patient, device):
    return FhirSource.objects.create(patient=patient, data_source=device, label="src")


def _aux(source, resource_type, fhir_resource_id, fhir_data, ref_indexed=False):
    return FhirAuxResource.objects.create(
        fhir_source=source,
        resource_type=resource_type,
        fhir_resource_id=fhir_resource_id,
        fhir_data=fhir_data,
        ref_indexed=ref_indexed,
    )


def test_index_rewrites_refs_to_jhe_ids(fhir_source):
    pat = _aux(fhir_source, "Patient", "pat-1", {"id": "pat-1"})
    enc = _aux(fhir_source, "Encounter", "enc-1", {"id": "enc-1", "subject": {"reference": "Patient/pat-1"}})

    summary = index_fhir_source_refs(fhir_source)

    enc.refresh_from_db()
    assert enc.fhir_data["subject"]["reference"] == f"Patient/{pat.pk}"
    assert enc.ref_indexed is True
    assert summary == {"rows_indexed": 2, "refs_rewritten": 1, "refs_not_found": 0}


def test_index_marks_unresolvable_refs_not_found(fhir_source):
    enc = _aux(fhir_source, "Encounter", "enc-2", {"id": "enc-2", "subject": {"reference": "Patient/missing"}})

    summary = index_fhir_source_refs(fhir_source)

    enc.refresh_from_db()
    assert enc.fhir_data["subject"]["reference"] == "not-found:missing"
    assert summary["refs_not_found"] == 1


def test_index_skips_already_indexed_rows(fhir_source):
    enc = _aux(
        fhir_source,
        "Encounter",
        "enc-3",
        {"id": "enc-3", "subject": {"reference": "Patient/x"}},
        ref_indexed=True,
    )

    summary = index_fhir_source_refs(fhir_source)

    enc.refresh_from_db()
    assert enc.fhir_data["subject"]["reference"] == "Patient/x"  # untouched
    assert summary["rows_indexed"] == 0


def test_create_aux_replaces_id_and_preserves_upstream(fhir_source):
    inst = create_aux_resource(
        "Encounter",
        {"resourceType": "Encounter", "id": "enc-up", "subject": {"reference": "Patient/pat-1"}},
        fhir_source,
    )
    inst.refresh_from_db()
    assert inst.fhir_resource_id == "enc-up"
    assert inst.fhir_data["id"] == str(inst.pk)
    assert inst.ref_indexed is False


def test_update_keeps_upstream_id_not_uuid(fhir_source):
    # Regression: a later write must not clobber fhir_resource_id with the JHE UUID (issue #584).
    from core.views.fhir import _persist_aux

    inst = _persist_aux(FhirAuxResource(), "Encounter", {"id": "enc-up", "status": "planned"}, fhir_source)
    assert inst.fhir_resource_id == "enc-up"
    assert inst.fhir_data["id"] == str(inst.pk)

    # Simulate an update whose body carries the JHE UUID (PATCH-merge of the stored body), no upstream id.
    _persist_aux(inst, "Encounter", {"id": str(inst.pk), "status": "finished"}, fhir_source)
    inst.refresh_from_db()
    assert inst.fhir_resource_id == "enc-up"  # preserved, not overwritten with the UUID


def test_index_is_idempotent(fhir_source):
    # Regression: re-indexing an already-rewritten ref must not flip it to not-found (issue #584).
    pat = _aux(fhir_source, "Patient", "pat-1", {"id": "pat-1"})
    enc = _aux(fhir_source, "Encounter", "enc-1", {"id": "enc-1", "subject": {"reference": "Patient/pat-1"}})

    index_fhir_source_refs(fhir_source)
    enc.refresh_from_db()
    resolved = enc.fhir_data["subject"]["reference"]
    assert resolved == f"Patient/{pat.pk}"

    # Re-run as if the row were re-saved (ref now holds the JHE UUID).
    FhirAuxResource.objects.filter(pk=enc.pk).update(ref_indexed=False)
    index_fhir_source_refs(fhir_source)
    enc.refresh_from_db()
    assert enc.fhir_data["subject"]["reference"] == resolved  # unchanged, not "not-found:..."


def test_index_refs_endpoint(fhir_source, patient):
    pat = _aux(fhir_source, "Patient", "pat-1", {"id": "pat-1"})
    enc = _aux(fhir_source, "Encounter", "enc-1", {"id": "enc-1", "subject": {"reference": "Patient/pat-1"}})
    api = APIClient()
    api.default_format = "json"
    api.force_authenticate(patient.jhe_user)

    r = api.post(f"/api/v1/fhir_sources/{fhir_source.id}/index_refs")

    assert r.status_code == 200, r.text
    # API renders camelCase, so the summary keys come back camelCased.
    assert r.json()["refsRewritten"] == 1
    enc.refresh_from_db()
    assert enc.fhir_data["subject"]["reference"] == f"Patient/{pat.pk}"
