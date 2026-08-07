import uuid

from django.db import models

from core.fhir.config import aux_resource_types
from core.fhir.scope import authorize_practitioner_scope, resolve_fhir_user

JHE_EXTENSION_BASE = "https://jupyterhealth.org/fhir/StructureDefinition"
# JHE provenance extension URLs -- stripped from a body before re-stamping so an update replaces
# them rather than accumulating duplicates.
JHE_AUX_EXTENSION_URLS = (
    f"{JHE_EXTENSION_BASE}/patient-id",
    f"{JHE_EXTENSION_BASE}/patient-full-name",
)

# ``meta.source`` values -- the single JHE-native-vs-imported discriminator used by both reads
# (the ``_source`` search param) and writes (naming the FhirSource an aux row came through).
#
#   * JHE_NATIVE_SOURCE -- stamped by the config mapping onto every JHE system-of-record (mapped)
#     resource. It deliberately does NOT live under ``/fhir`` because those rows are JHE-native,
#     not arrived-via-FHIR. It must stay in sync with the ``meta.source`` literal in
#     fhir_config.json's mapped_resources.
#   * JHE_FHIR_SOURCE_BASE/<id> -- stamped onto every aux row, identifying the FhirSource (by pk)
#     it was ingested through. Imported rows all nest under this prefix so a single
#     ``_source:below=<base>/`` (a string-prefix match) selects "everything imported", while an
#     exact ``_source=<base>/<id>`` selects one source. A JHE-minted URI (not the upstream
#     ``fhir_base_url``) is used so the values are homogeneous, collision-free, and groupable.
JHE_NATIVE_SOURCE = "https://jupyterhealth.org/jhe"
JHE_FHIR_SOURCE_BASE = "https://jupyterhealth.org/fhir/fhir-source"


def fhir_source_uri(source_id):
    """The canonical ``meta.source`` URI for a FhirSource pk (``<base>/<id>``)."""
    return f"{JHE_FHIR_SOURCE_BASE}/{source_id}"


def parse_fhir_source_id(uri):
    """Return the integer FhirSource pk encoded in a ``<base>/<id>`` ``meta.source`` URI, else None.

    Only the exact ``JHE_FHIR_SOURCE_BASE/<digits>`` shape resolves; the JHE-native URI, an
    upstream/external URI, or anything malformed yields ``None`` (the caller then falls back or
    treats it as unmatched).
    """
    if not isinstance(uri, str):
        return None
    prefix = f"{JHE_FHIR_SOURCE_BASE}/"
    if not uri.startswith(prefix):
        return None
    tail = uri.removeprefix(prefix)
    return int(tail) if tail.isdigit() else None


def apply_jhe_extensions(body, fhir_source):
    """Stamp ``body`` with JHE provenance for ``fhir_source`` -- ``meta.source`` + patient extensions.

    ``meta.source`` is (over)written to the canonical FhirSource URI (``fhir_source_uri``), the
    authoritative record of which source an aux row came through, and drives the ``_source`` read
    routing. Two patient-attribution extensions -- the owning patient's pk and (when set) full name
    -- carry the patient, since ``meta.source`` names the *source system*, not the patient, and the
    opaque body may not otherwise resolve to the JHE patient. Any prior copies of the JHE extensions
    are dropped first so re-stamping (an update) replaces rather than accumulates. Mutates and
    returns ``body``.
    """
    patient = fhir_source.patient
    full_name = " ".join(part for part in (patient.name_given, patient.name_family) if part)
    meta = dict(body.get("meta") or {})
    meta["source"] = fhir_source_uri(fhir_source.pk)
    body["meta"] = meta
    others = [ext for ext in (body.get("extension") or []) if ext.get("url") not in JHE_AUX_EXTENSION_URLS]
    extensions = [{"url": f"{JHE_EXTENSION_BASE}/patient-id", "valueInteger": patient.pk}]
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
    uploaded through, named on write by the ``X-JHE-FHIR-Source-ID`` header (authoritative) or the
    body's ``meta.source`` and recorded on the stored body as ``meta.source`` -- which in turn
    carries the owning ``patient``. ``fhir_resource_id`` and ``patient_fhir_id`` are best-effort
    copies of the resource's own ``id`` and its referenced Patient id (both may be null).
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
        constraints = [
            # One aux row per upstream record: create_aux_resource (core/views/fhir.py) updates
            # in place on re-import, and this backs it against races. Rows with no upstream id
            # cannot be identified across imports and are exempt.
            models.UniqueConstraint(
                fields=["fhir_source", "resource_type", "fhir_resource_id"],
                condition=models.Q(fhir_resource_id__isnull=False) & ~models.Q(fhir_resource_id=""),
                name="uniq_aux_upstream_record_per_source",
            ),
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
        fhir_source_id=None,
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
        # UUID. fhir_source_id narrows to a single upstream source (the `_source=<base>/<id>` read
        # route); like an identifier it is an unauthorized predicate -- the organization/patient
        # join already scopes the result, so an inaccessible source simply yields nothing.
        # distinct() collapses the duplicate rows produced by spanning the patient's
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
        if fhir_source_id:
            qs = qs.filter(fhir_source_id=fhir_source_id)

        return qs.distinct().order_by("-last_updated")
