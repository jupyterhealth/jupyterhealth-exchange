import uuid

from django.db import models

from core.fhir.config import aux_resource_types
from core.fhir.scope import authorize_practitioner_scope, resolve_fhir_user

JHE_EXTENSION_BASE = "https://jupyterhealth.org/fhir/StructureDefinition"
JHE_AUX_EXTENSION_URLS = (
    f"{JHE_EXTENSION_BASE}/fhir-source-id",
    f"{JHE_EXTENSION_BASE}/patient-id",
    f"{JHE_EXTENSION_BASE}/patient-full-name",
)


def apply_jhe_extensions(body, fhir_source):
    """Stamp ``body`` with the JHE provenance extensions for ``fhir_source``.

    Every stored aux body carries the FhirSource pk, the owning patient's pk, and (when set) the
    patient's full name, so a reader can attribute an opaque aux body to its source and patient
    without a join. Any prior copies are dropped first so re-stamping (an update, or a backfill)
    replaces current values rather than accumulating duplicates. Mutates and returns ``body``.
    """
    patient = fhir_source.patient
    full_name = " ".join(part for part in (patient.name_given, patient.name_family) if part)
    others = [ext for ext in (body.get("extension") or []) if ext.get("url") not in JHE_AUX_EXTENSION_URLS]
    extensions = [
        {"url": f"{JHE_EXTENSION_BASE}/fhir-source-id", "valueInteger": fhir_source.pk},
        {"url": f"{JHE_EXTENSION_BASE}/patient-id", "valueInteger": patient.pk},
    ]
    if full_name:
        extensions.append({"url": f"{JHE_EXTENSION_BASE}/patient-full-name", "valueString": full_name})
    body["extension"] = others + extensions
    return body


class FhirAuxResource(models.Model):
    """An *auxiliary* FHIR resource stored as an opaque JSON blob.

    Every FHIR resource that does not fit the JHE-system view of a mapped Django model is
    stored here: its entire FHIR body lives in ``fhir_data`` and the server performs no
    computation on it beyond plain CRUD. The supported resource types are declared under
    ``aux_resources`` in core/fhir/fhir_config.json (mapped types appear there too -- their
    non-system rows fall through to this store).

    The primary key (and therefore the FHIR-facing ``id``) is a UUID, keeping it disjoint
    from the integer pks of the mapped models so a request can be routed by id shape. Every row
    is linked to a ``fhir_source`` (required) -- the patient-registered upstream source it was
    uploaded through, identified by the ``X-JHE-FHIR-Source-ID`` header -- which in turn carries
    the owning ``patient``. ``fhir_resource_id`` and ``patient_fhir_id`` are best-effort copies of
    the resource's own ``id`` and its referenced Patient id (both may be null).
    """

    # Not passed to the field — keeps choices out of migration state so adding/removing
    # resource types in fhir_config.json never requires a migration.
    RESOURCE_TYPE_CHOICES = [(name, name) for name in sorted(aux_resource_types())]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fhir_source = models.ForeignKey("FhirSource", on_delete=models.CASCADE, related_name="aux_resources")
    resource_type = models.CharField()
    patient_fhir_id = models.CharField(null=True, blank=True)
    fhir_resource_id = models.CharField(null=True, blank=True)
    fhir_data = models.JSONField(null=True)
    # False after any write; the index-refs pass (issue #584) rewrites this row's references
    # from upstream ids to JHE ids and flips it True so it is not re-processed.
    ref_indexed = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["resource_type", "fhir_source"]),
        ]

    def __str__(self):
        return f"{self.resource_type}/{self.pk}"

    @staticmethod
    def for_patient(patient, resource_type):
        # Auxiliary resources for a single patient (the resolved FhirSource patient).
        return FhirAuxResource.objects.filter(resource_type=resource_type, fhir_source__patient=patient).order_by(
            "-last_updated"
        )

    @staticmethod
    def fhir_search(
        jhe_user_id,
        resource_type,
        resource_id=None,
        organization_id=None,
        study_id=None,
        patient_id=None,
        **params,
    ):
        # Return the auxiliary resources of `resource_type` visible to the user, as a queryset of
        # FhirAuxResource rows. Each row reaches its owning patient through its FhirSource
        # (FhirAuxResource -> FhirSource -> Patient), so every filter is expressed against
        # `fhir_source__patient`. This mirrors the mapped models' normalized fhir_search: a
        # patient user sees only their own rows (the organization/study/patient filters are
        # ignored); a practitioner sees rows whose patient shares one of their organizations --
        # narrowed to an organization, to a study (its enrolled patients), or to a single patient
        # (each authorized up front, 403 on mismatch). resource_id selects a single row by its
        # UUID. distinct() collapses the duplicate rows produced by spanning the patient's
        # organization/study many-to-many relationships. **params is reserved for additional FHIR
        # search predicates.
        user = resolve_fhir_user(jhe_user_id)
        if user.is_patient():
            qs = FhirAuxResource.objects.filter(
                resource_type=resource_type, fhir_source__patient__jhe_user_id=jhe_user_id
            )
        else:
            authorize_practitioner_scope(jhe_user_id, organization_id, study_id, patient_id)
            qs = FhirAuxResource.objects.filter(
                resource_type=resource_type,
                fhir_source__patient__organizations__practitioners__jhe_user_id=jhe_user_id,
            )
            if organization_id:
                qs = qs.filter(fhir_source__patient__organizations__id=organization_id)
            if study_id:
                qs = qs.filter(fhir_source__patient__studypatient__study_id=study_id)
            if patient_id:
                qs = qs.filter(fhir_source__patient_id=patient_id)

        if resource_id:
            qs = qs.filter(id=resource_id)

        return qs.distinct().order_by("-last_updated")
