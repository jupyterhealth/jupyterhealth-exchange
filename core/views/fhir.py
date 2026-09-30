"""Unified FHIR R5 resource endpoint.

A single view, ``FHIRResourceView``, serves every supported FHIR resource at
``FHIR/<version>/<resource>`` (and ``.../<resource>/<id>``), where ``<version>`` comes
from core/fhir/fhir_config.json (e.g. ``FHIR/R5/Patient``).

Routing is config-driven (see fhir_engine.md). A Django model backs only the JHE-system
view of each resource; everything else lives in the generic ``FhirAuxResource`` store:

  * **search** hits exactly ONE store, chosen by the ``_source`` search param (there is no
    cross-store union): absent or the JHE-native URI -> the Django-mapped rows; a
    ``<fhir-source-base>/<id>`` URI -> that source's aux rows; ``_source:below=<fhir-source-base>/``
    -> every imported aux row. See ``_resolve_source_target``.
  * **read / update / delete** are routed by id shape -- a UUID id targets FhirAuxResource,
    an integer id targets the mapped Django model.
  * **create** is routed by the resource's ``__interaction`` allow-list and, where present,
    its ``__criteria``: only an OMH Observation writes to the Django model; every other
    write lands in FhirAuxResource, linked to the FhirSource named by the ``X-JHE-FHIR-Source-ID``
    header (authoritative when present) or else the body's ``meta.source`` URI. A bundle-level
    ``meta.source`` is never consulted; the ``_source`` header is write-only (ignored on reads).

The FHIR bundle batch endpoint (POST at the base, e.g. ``FHIR/R5/``) remains served by FHIRBase.
"""

import logging
import uuid

from django.core.exceptions import BadRequest as DjangoBadRequest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.cache import cache_control
from rest_framework import status as http_status
from rest_framework.decorators import api_view, permission_classes, renderer_classes
from rest_framework.exceptions import APIException, MethodNotAllowed, NotFound
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from core.auth import authorize_uri, token_uri
from core.fhir.capability import build_capability_statement
from core.fhir.config import (
    aux_interactions,
    get_config_errors,
    get_resource_mapping,
    is_aux_resource,
    is_mapped_resource,
    is_supported_resource,
    mapped_criteria,
    mapped_interactions,
    mapped_model_name,
)
from core.fhir.engine import build_fhir_resource, matches_criteria
from core.fhir.fhir_validation import supports_identifier, validate_fhir_resource
from core.fhir.pagination import FHIRBundlePagination
from core.fhir.search import apply_search_params, summary_count_requested
from core.models import (
    JHE_FHIR_SOURCE_BASE,
    JHE_NATIVE_SOURCE,
    FhirAuxResource,
    FhirSource,
    Observation,
    Patient,
    apply_jhe_extensions,
    fhir_source_uri,
    parse_fhir_source_id,
)
from core.serializers import FHIRAuxResourceSerializer, FHIRObservationSerializer
from core.views.fhir_base import FHIR_RENDERER_CLASSES, FHIRBase

logger = logging.getLogger(__name__)

FHIR_SOURCE_ID_HEADER = "X-JHE-FHIR-Source-ID"


# ---------------------------------------------------------------------------
# Mapped resource handlers (read/search against a Django model)
# ---------------------------------------------------------------------------


def _canonical_search_kwargs(request):
    """Translate the canonical FHIR search params into ``fhir_search`` kwargs.

    Shared by the mapped and auxiliary handlers. Every supported resource accepts the
    ``patient`` / ``patient.organization`` / ``patient._has:Group:member`` location filters;
    Patient/Observation additionally accept an ``identifier`` and Observation a ``code``, passed
    through as ``**params`` (each model ignores the keys it does not use).

    NOTE: the client sends the FHIR-standard ``patient._has:Group:member:_id`` (capital "Group"),
    but the djangorestframework-camel-case parser snake-cases every incoming query-param key
    before it reaches request.GET, so "Group" arrives as "_group". We therefore read
    ``patient._has:_group:member:_id``. The other keys (patient, patient.organization, identifier,
    code) are already lowercase and pass through as-is.
    """
    identifier = request.GET.get("identifier") or request.GET.get("patient.identifier")
    code = request.GET.get("code")
    kwargs = {
        "organization_id": request.GET.get("patient.organization"),
        "study_id": request.GET.get("patient._has:_group:member:_id"),
        "patient_id": request.GET.get("patient"),
    }
    if identifier:
        system, _, value = identifier.partition("|")
        kwargs["patient_identifier_system"] = system
        kwargs["patient_identifier_value"] = value
    if code:
        # FHIR search: comma-separated token values OR together, each one a system|code
        # (a bare code has no system), so pass them as a list of pairs.
        kwargs["coding_pairs"] = [_split_coding(piece) for piece in code.split(",") if piece.strip()]
    return kwargs


def _split_coding(token):
    if "|" in token:
        system, _, value = token.strip().partition("|")
        return system, value
    return None, token.strip()


# US Core date-comparator prefixes -> Django ORM lookups (issue #585).
_DATE_COMPARATORS = {"ge": "gte", "le": "lte", "gt": "gt", "lt": "lt"}


def _last_updated_values(request):
    # The camel-case parser may snake-case the query key, so accept both spellings.
    return request.GET.getlist("_lastUpdated") + request.GET.getlist("_last_updated")


def apply_common_search_filters(queryset, request):
    """Apply the resource-agnostic US Core search params ``_id`` and ``_lastUpdated`` (issue #585).

    Works on any search queryset: every mapped model and FhirAuxResource exposes ``id`` and
    ``last_updated``. ``_id`` is an exact match (an id of the wrong shape for this store yields no
    rows, so a UUID id matches only the aux source and an integer id only the mapped source).
    ``_lastUpdated`` supports the ``ge``/``le``/``gt``/``lt`` comparators, or a bare date meaning
    that whole calendar day; repeated ``_lastUpdated`` params AND together to express a range.
    """
    resource_id = request.GET.get("_id")
    if resource_id:
        try:
            queryset.model._meta.pk.to_python(resource_id)
        except (ValueError, TypeError, DjangoValidationError):
            return queryset.none()
        queryset = queryset.filter(pk=resource_id)

    for raw in _last_updated_values(request):
        comparator = _DATE_COMPARATORS.get(raw[:2])
        value = raw[2:] if comparator else raw
        dt = parse_datetime(value)
        if dt is not None:
            # A datetime value: compare against the instant (made aware to avoid naive warnings).
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            lookup = f"last_updated__{comparator}" if comparator else "last_updated__date"
            queryset = queryset.filter(**{lookup: (dt if comparator else dt.date())})
            continue
        day = parse_date(value)
        if day is None:
            raise DRFValidationError(f"Invalid _lastUpdated value: '{raw}'.")
        # A date-only value: compare the date part (day-granular, matches FHIR date precision
        # and sidesteps naive-datetime warnings on the DateTimeField).
        lookup = f"last_updated__date__{comparator}" if comparator else "last_updated__date"
        queryset = queryset.filter(**{lookup: day})
    return queryset


class MappedResourceHandler:
    """Renders a mapped resource's Django rows via the model's normalized ``fhir_search``.

    Every mapped model exposes ``fhir_search(jhe_user_id, resource_id=None,
    organization_id=None, study_id=None, patient_id=None, **params)``: it resolves the
    requesting user (patient vs practitioner), applies organization-membership authorization
    (an unauthorized organization/study/patient -> 403), and returns a queryset of model
    instances. The handler is therefore generic -- it only translates the canonical FHIR
    query params into those kwargs and renders each instance through the config mapping.
    ``ObservationHandler`` subclasses this to override serialization (Base64) and add the
    OMH/IEEE create/update/delete paths; update/delete are otherwise unimplemented (a config
    declaring them for a mapped resource does not by itself make them work).
    """

    def __init__(self, resource_type, request):
        self.resource_type = resource_type
        self.request = request
        self.user = request.user

    def _model(self):
        from django.apps import apps

        return apps.get_model("core", mapped_model_name(self.resource_type))

    def serialize(self, instance):
        return build_fhir_resource(instance, self.resource_type, get_resource_mapping(self.resource_type))

    def search(self):
        return self._model().fhir_search(self.user.id, **_canonical_search_kwargs(self.request))

    def read(self, fhir_id):
        instance = self._model().fhir_search(self.user.id, resource_id=fhir_id).first()
        if instance is None:
            raise NotFound(f"{self.resource_type}/{fhir_id} not found.")
        return instance

    def update(self, fhir_id, data, partial=False):
        raise MethodNotAllowed(
            "PATCH" if partial else "PUT", detail=f"Update of a {self.resource_type} record is not supported."
        )

    def delete(self, fhir_id):
        raise MethodNotAllowed("DELETE", detail=f"Delete of a {self.resource_type} record is not supported.")


class ObservationHandler(MappedResourceHandler):
    """Observation needs a custom serializer (Base64 valueAttachment) and the OMH/IEEE
    create/update/delete paths."""

    def serialize(self, instance):
        # valueAttachment.data needs Base64 encoding, which the config can't express.
        return FHIRObservationSerializer().to_representation(instance)

    def create(self, data):
        # Only the OMH/IEEE path reaches here (the view routes other-coded Observations to aux).
        observation = Observation.fhir_create(data, self.user)
        logger.debug("created observation: %s", observation)

        # Return a minimal FHIR-compliant response for the patient path (avoids re-rendering
        # the full search/serializer for the single just-created row).
        if self.user.is_patient():
            obs = Observation.objects.select_related("subject_patient", "codeable_concept").get(pk=observation.id)
            return {
                "resourceType": "Observation",
                "id": str(obs.id),
                "status": "final",
                "meta": {"lastUpdated": obs.last_updated.isoformat() if obs.last_updated else None},
                "subject": {"reference": f"Patient/{obs.subject_patient_id}"},
                "code": {
                    "coding": [{"system": obs.codeable_concept.coding_system, "code": obs.codeable_concept.coding_code}]
                },
            }

        fhir_observation = Observation.fhir_search(self.user.id, resource_id=observation.id).first()
        return self.serialize(fhir_observation)

    def update(self, fhir_id, data, partial=False):
        observation = self._authorized_instance(fhir_id)
        Observation.fhir_update(observation, data, self.user, partial=partial)
        fhir_observation = Observation.fhir_search(self.user.id, resource_id=observation.id).first()
        return self.serialize(fhir_observation)

    def delete(self, fhir_id):
        observation = self._authorized_instance(fhir_id)
        Observation.fhir_delete(observation, self.user)

    def _authorized_instance(self, fhir_id):
        # A plain pk lookup, not fhir_search -- fhir_update/fhir_delete do their own
        # authorization (Observation._authorize_subject, the same guard fhir_create uses), so
        # this only needs to find the row (404 if it doesn't exist) and hand it over.
        try:
            return Observation.objects.select_related("subject_patient", "codeable_concept").get(pk=fhir_id)
        except (Observation.DoesNotExist, ValueError, TypeError):
            raise NotFound(f"{self.resource_type}/{fhir_id} not found.")


# Mapped resources use the generic handler unless they need custom behavior.
_MAPPED_HANDLERS = {
    "Observation": ObservationHandler,
}


# ---------------------------------------------------------------------------
# Auxiliary resource handler (opaque JSON-blob CRUD in FhirAuxResource)
# ---------------------------------------------------------------------------


class AuxResourceHandler:
    """Opaque JSON-blob CRUD.

    A **write** (create/update/delete) requires the ``X-JHE-FHIR-Source-ID`` header, which
    resolves the FhirSource (and its patient) the row is linked to. A **read** (search/read)
    scopes to that source's patient when the header is present, and otherwise returns every
    resource the user can access (a practitioner's org patients, or a patient user's own).
    """

    def __init__(self, resource_type, request):
        self.resource_type = resource_type
        self.request = request
        self.user = request.user

    # -- source / patient context --

    def _write_context(self, resource=None):
        # Writes must name a source -- the header (authoritative) or the body's meta.source:
        # (patient, fhir_source). Raises 400 if neither is present, 403/400 if the source is
        # unknown or the user may not use it.
        return resolve_fhir_source_context(self.request, self.user, resource)

    def _read_scope(self):
        # Reads go through the normalized FhirAuxResource.fhir_search (patient-vs-practitioner +
        # org-membership authorization), exactly like the mapped handler. The source header is
        # write-only -- it is ignored here; table + single-source selection is done by the view from
        # the _source param (see FHIRResourceView._resolve_source_target). What remains is the
        # canonical patient / patient.organization / patient._has:Group:member location filters, so
        # e.g. ?patient._has:Group:member:_id=<study> returns the aux rows of that resource type for
        # the study's patients the practitioner can access.
        return _canonical_search_kwargs(self.request)

    def serialize(self, instance):
        return FHIRAuxResourceSerializer().to_representation(instance)

    def search(self, fhir_source_id=None):
        return FhirAuxResource.fhir_search(
            self.user.id, self.resource_type, fhir_source_id=fhir_source_id, **self._read_scope()
        )

    def read(self, fhir_id):
        instance = FhirAuxResource.fhir_search(
            self.user.id, self.resource_type, resource_id=fhir_id, **self._read_scope()
        ).first()
        if instance is None:
            raise NotFound(f"{self.resource_type}/{fhir_id} not found.")
        return self.serialize(instance)

    def create(self, data):
        # The camel-case parser snake-cases incoming JSON; restore FHIR camelCase before
        # validating and storing so fhir_data round-trips as valid FHIR. The write context
        # resolves first because the upstream id moves into an identifier namespaced by the
        # source, which must therefore be known before the body is validated.
        data = _camelized(data)
        _, fhir_source = self._write_context(data)
        data, upstream_id = _extract_upstream_id(self.resource_type, data, fhir_source)
        validate_fhir_resource(self.resource_type, data)
        return self.serialize(create_aux_resource(self.resource_type, data, fhir_source, upstream_id=upstream_id))

    def update(self, fhir_id, data, partial=False):
        camel = _camelized(data)
        patient, fhir_source = self._write_context(camel)
        instance = self._write_instance(fhir_id, patient)
        body = _aux_body(camel)
        if partial:
            merged = dict(instance.fhir_data or {})
            merged.update(body)
            body = merged
        validate_fhir_resource(self.resource_type, {**body, "resourceType": self.resource_type})
        # An update may move this row onto an upstream id a sibling already holds; that is the same
        # uniqueness violation a create hits and is reported the same way.
        return self.serialize(
            _save_aux(
                instance,
                self.resource_type,
                body,
                fhir_source,
                None,
                "Update that record instead, or use an upstream id not already taken under this source.",
            )
        )

    def delete(self, fhir_id):
        patient, _ = self._write_context()
        self._write_instance(fhir_id, patient).delete()

    def _write_instance(self, fhir_id, patient):
        # A write targets a record under the named source's patient.
        try:
            return FhirAuxResource.for_patient(patient, self.resource_type).get(pk=fhir_id)
        except (FhirAuxResource.DoesNotExist, ValueError, TypeError):
            raise NotFound(f"{self.resource_type}/{fhir_id} not found.")


def _source_id_from_body(resource):
    """The FhirSource pk named by a resource body's ``meta.source`` URI, or None.

    Only the resource's *own* ``meta.source`` is read; a bundle-level ``meta.source`` never
    propagates to its entries (FHIR defines no such inheritance), so callers pass the entry
    resource, not the enclosing Bundle.
    """
    if not isinstance(resource, dict):
        return None
    return parse_fhir_source_id((resource.get("meta") or {}).get("source"))


def resolve_fhir_source_context(request, user, resource=None):
    """Resolve ``(patient, fhir_source)`` for a write.

    The source is named by the ``X-JHE-FHIR-Source-ID`` header (**authoritative** when present)
    or, failing that, by the resource body's ``meta.source`` URI (``<fhir-source-base>/<id>``).
    A bundle-level ``meta.source`` is never consulted. The patient is taken from the user's own
    access token (patient users) or from the source's patient (non-patient users). Naming no
    source at all -> 400; unknown source -> 400; a source the user may not use -> 403.
    """
    source_id = request.headers.get(FHIR_SOURCE_ID_HEADER) or _source_id_from_body(resource)
    if not source_id:
        raise DRFValidationError(
            f"A FhirSource is required to write this resource: send the '{FHIR_SOURCE_ID_HEADER}' "
            f"header or set the resource's meta.source to '{JHE_FHIR_SOURCE_BASE}/<id>'."
        )
    try:
        fhir_source = FhirSource.objects.select_related("patient").get(pk=source_id)
    except (FhirSource.DoesNotExist, ValueError, TypeError):
        raise DRFValidationError(f"FhirSource '{source_id}' does not exist.")

    if user.is_patient():
        # Patient users are scoped to themselves via the access token; the source must be theirs.
        own = user.get_patient()
        if own is None:
            raise DRFPermissionDenied("Current user is not a Patient.")
        if fhir_source.patient_id != own.id:
            raise DRFPermissionDenied("The FhirSource does not belong to the current user.")
        return own, fhir_source

    # Non-patient (practitioner): derive the patient from the source, with org-sharing authz
    # plus the "patient.manage_data" permission -- organization membership alone is not enough
    # for a write (a `viewer` shares the organization but the role table denies it), the same
    # pair of checks Observation.fhir_create/_authorize_subject applies to the mapped OMH/IEEE
    # path.
    if not Patient.practitioner_authorized(user.id, fhir_source.patient_id):
        raise DRFPermissionDenied("Current user does not have access to this FhirSource's patient.")
    if not Patient.practitioner_can_manage_data(user, fhir_source.patient_id):
        raise DRFPermissionDenied("Current user's role does not include permission to manage this Patient's data.")
    return fhir_source.patient, fhir_source


def _aux_body(data):
    # Store the FHIR body verbatim minus resourceType (it is derived from the column).
    return {key: value for key, value in dict(data).items() if key != "resourceType"}


def _extract_upstream_id(resource_type, data, fhir_source):
    """Move the upstream record's ``id`` out of the body and into an ``identifier``.

    On a FHIR create the logical ``id`` is the server's to assign and the client's is ignored, so
    an incoming ``id`` (an EHR record arrives carrying the id its own server minted) belongs in
    ``identifier``, the cross-system business identifier. The namespace is the FhirSource's URI --
    the same value stamped onto ``meta.source`` -- because an upstream id is only unique *within*
    the source it came from, and a source is identified by its pk and nothing else.

    Doing this for every id, not just spec-violating ones, also side-steps Epic's "Unconstrained
    FHIR IDs": ids over FHIR's 64-char limit would fail R5 validation in the body but are fine in
    an identifier, and ``fhir_resource_id`` (which the uniqueness constraint is enforced on) has
    no length limit either. Returns ``(possibly-copied data, upstream_id_or_None)``.

    A resource type with no ``identifier`` element at all (Provenance; Binary, were it ever
    configured) has nowhere to put it -- ``fhir.resources`` forbids extra fields, so writing one
    would fail validation. The id still leaves the body and still keys uniqueness through the
    ``fhir_resource_id`` column; it is simply not represented in the stored body.
    """
    upstream_id = data.get("id")
    if not (isinstance(upstream_id, str) and upstream_id):
        return data, None
    data = dict(data)
    if supports_identifier(resource_type):
        identifiers = list(data.get("identifier") or [])
        stamp = {"system": fhir_source_uri(fhir_source.pk), "value": upstream_id}
        if stamp not in identifiers:
            identifiers.append(stamp)
        data["identifier"] = identifiers
    del data["id"]
    return data, upstream_id


def _derive_patient_fhir_id(resource_type, body, upstream_id=None):
    # Best-effort extraction of the resource's referenced Patient id (may be None).
    if resource_type == "Patient":
        # On a create the resource's own id has been moved into an identifier, so it arrives as
        # ``upstream_id``; on an update the (already-merged) body still carries it.
        return upstream_id or body.get("id")
    for key in ("subject", "patient", "beneficiary"):
        node = body.get(key)
        reference = node.get("reference") if isinstance(node, dict) else None
        if isinstance(reference, str) and reference.startswith("Patient/"):
            return reference.split("/", 1)[1]
    return None


def _persist_aux(instance, resource_type, body, fhir_source, upstream_id=None):
    body = apply_jhe_extensions(_aux_body(body), fhir_source)
    instance.resource_type = resource_type
    instance.fhir_source = fhir_source
    instance.patient_fhir_id = _derive_patient_fhir_id(resource_type, body, upstream_id)
    # Preserve the upstream id, then expose the JHE UUID as the body's own id so the stored body
    # matches its JHE-facing id (issue #584). The pk exists pre-save (UUID default at init).
    # On update the incoming id is already this row's JHE UUID (written on create, or merged from
    # the stored body on a PATCH), so only capture it as the upstream id when it differs from the
    # pk -- otherwise an update would clobber the real upstream id with our own UUID. On a
    # create the id has already been moved out of the body into an identifier by
    # _extract_upstream_id and arrives here as ``upstream_id``.
    incoming_id = upstream_id or body.get("id")
    if incoming_id and incoming_id != str(instance.pk):
        instance.fhir_resource_id = incoming_id
    body["id"] = str(instance.pk)
    instance.fhir_data = body
    # Any write invalidates prior ref indexing; the index-refs pass re-processes this row.
    instance.ref_indexed = False
    instance.save()
    # Audit trail of ingested clinical resources (REQ-SYNC-07).
    logger.info(
        "aux ingest: %s/%s patient=%s source=%s", resource_type, instance.pk, fhir_source.patient_id, fhir_source.pk
    )
    return instance


class DuplicateAuxResource(APIException):
    """409 for a write whose upstream record id is already taken under this FhirSource.

    Upstream record ids are unique within a source, so a create can never land on an existing
    record and an update can never take another row's id. The client is told which row it
    collided with and decides what to do.
    """

    status_code = http_status.HTTP_409_CONFLICT
    default_code = "duplicate"

    def __init__(self, resource_type, upstream_id, fhir_source, existing_id, remedy):
        super().__init__(
            {
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "duplicate",
                        "diagnostics": (
                            f"{resource_type} with upstream id '{upstream_id}' is already stored under "
                            f"FhirSource {fhir_source.pk} as {resource_type}/{existing_id}. Upstream record "
                            f"ids are unique within a source. {remedy}"
                        ),
                    }
                ],
            }
        )


def _save_aux(instance, resource_type, body, fhir_source, upstream_id, remedy):
    """Persist an aux row, turning a uniqueness violation into a 409 that names the collision."""
    # Mirrors how _persist_aux picks the upstream id: a create passes it in (the body's own id was
    # already moved into an identifier), an update leaves it in the body.
    upstream_id = upstream_id or body.get("id")
    try:
        # Atomic so a rejected write cannot poison a transaction the caller is running in.
        with transaction.atomic():
            return _persist_aux(instance, resource_type, body, fhir_source, upstream_id=upstream_id)
    except IntegrityError:
        existing = (
            FhirAuxResource.objects.filter(
                fhir_source=fhir_source, resource_type=resource_type, fhir_resource_id=upstream_id
            )
            .exclude(pk=instance.pk)
            .first()
            if upstream_id
            else None
        )
        if existing is None:
            raise  # Not the uniqueness constraint -- a real database error, not a duplicate.
        raise DuplicateAuxResource(resource_type, upstream_id, fhir_source, existing.pk, remedy)


def create_aux_resource(resource_type, data, fhir_source, upstream_id=None):
    """Create a FhirAuxResource of ``resource_type`` linked to ``fhir_source`` (and its patient).

    A create always creates. Re-posting a record already stored under this source is refused with
    409 rather than silently refreshing it or silently duplicating it -- POST is a FHIR create, not
    an upsert, and the caller is the one who knows whether it meant to update. Uniqueness is per
    (source, type, upstream id) and enforced by the constraint on FhirAuxResource; a body with no
    upstream id cannot be recognised at all and always creates.
    """
    remedy = (
        "A create never replaces an existing record: update that record instead, or register a separate FhirSource."
    )
    return _save_aux(FhirAuxResource(), resource_type, _aux_body(data), fhir_source, upstream_id, remedy)


# ---------------------------------------------------------------------------
# The unified view
# ---------------------------------------------------------------------------


class _ConfigError(APIException):
    status_code = http_status.HTTP_500_INTERNAL_SERVER_ERROR


class FHIRResourceView(APIView):
    """Dispatches an HTTP verb on ``FHIR/<version>/<resource>[/<id>]`` to the right backing store.

    Each request maps to a FHIR interaction (search/read/create/update/delete) and is routed to
    the mapped Django model and/or the FhirAuxResource store per the config (see module docstring).
    """

    renderer_classes = FHIR_RENDERER_CLASSES

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        errors = get_config_errors()
        if errors:
            raise _ConfigError("Invalid FHIR configuration: " + "; ".join(errors))

    def _check_supported(self, resource):
        if not is_supported_resource(resource):
            raise NotFound(f"Unsupported FHIR resource type: {resource}.")

    @staticmethod
    def _is_aux_id(fhir_id):
        # FhirAuxResource pks are UUIDs; mapped models use integer pks. Route by id shape.
        try:
            uuid.UUID(str(fhir_id))
            return True
        except (ValueError, AttributeError, TypeError):
            return False

    def _mapped_handler(self, resource):
        if not is_mapped_resource(resource):
            raise NotFound(f"Unsupported FHIR resource type: {resource}.")
        handler_cls = _MAPPED_HANDLERS.get(resource, MappedResourceHandler)
        return handler_cls(resource, self.request)

    def _aux_handler(self, resource):
        return AuxResourceHandler(resource, self.request)

    def _refuse(self, resource, interaction):
        raise MethodNotAllowed(
            self.request.method, detail=f"The '{interaction}' interaction is not allowed for {resource}."
        )

    # -- HTTP verbs --

    def get(self, request, resource, id=None):
        self._check_supported(resource)
        if id is None:
            self._remember_search(resource)
            return self._search_bundle(resource)
        return Response(self._read(resource, id))

    # Sent only by the jhe-admin browser's FHIR page. Searches from anything else (the MCP
    # server, API scripts) must not clobber the practitioner's sticky selections.
    REMEMBER_VIEW_HEADER = "X-JHE-Remember-View"

    def _remember_search(self, resource):
        # Make the admin UI's FHIR-browser controls sticky, the same way the studies/
        # observations/patients list endpoints persist current_organization_id and
        # current_study_id (see Practitioner.remember_settings). One setting per control:
        # the resource type, the Source select, and the Patient ID box. Paging is deliberately
        # not remembered -- returning to page 7 of a different search helps nobody.
        if not self.request.headers.get(self.REMEMBER_VIEW_HEADER):
            return
        user = self.request.user
        if hasattr(user, "practitioner_profile"):
            user.practitioner_profile.remember_settings(
                save={
                    "current_fhir_resource": resource,
                    "current_fhir_source": self.request.GET.get("_source:below"),
                    "current_fhir_jhe_patient_id": self.request.GET.get("patient"),
                },
                # An absent source is the "None (JHE System Data)" selection and an absent
                # patient is a cleared filter -- both are choices, so neither may go stale.
                forget=("current_fhir_source", "current_fhir_jhe_patient_id"),
            )

    def post(self, request, resource, id=None):
        self._check_supported(resource)
        if id == "_search":  # FHIR search via POST
            return self._search_bundle(resource)
        if id is not None:
            raise MethodNotAllowed("POST")
        return Response(self._create(resource, request.data), status=http_status.HTTP_201_CREATED)

    def put(self, request, resource, id=None):
        self._check_supported(resource)
        if id is None:
            raise MethodNotAllowed("PUT", detail="An id is required to update a resource.")
        return Response(self._update(resource, id, request.data, partial=False))

    def patch(self, request, resource, id=None):
        self._check_supported(resource)
        if id is None:
            raise MethodNotAllowed("PATCH", detail="An id is required to update a resource.")
        return Response(self._update(resource, id, request.data, partial=True))

    def delete(self, request, resource, id=None):
        self._check_supported(resource)
        if id is None:
            raise MethodNotAllowed("DELETE", detail="An id is required to delete a resource.")
        self._destroy(resource, id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)

    # -- routed operations --

    def _read(self, resource, fhir_id):
        if self._is_aux_id(fhir_id):
            if "read" not in aux_interactions(resource):
                self._refuse(resource, "read")
            return self._aux_handler(resource).read(fhir_id)
        if "read" not in mapped_interactions(resource):
            self._refuse(resource, "read")
        handler = self._mapped_handler(resource)
        return handler.serialize(handler.read(fhir_id))

    def _create(self, resource, data):
        criteria = mapped_criteria(resource)
        mapped = mapped_interactions(resource)
        aux = aux_interactions(resource)
        # The code criteria routes a writable mapped resource between the model and aux.
        if "create" in mapped and (criteria is None or matches_criteria(_camelized(data), criteria)):
            return self._mapped_handler(resource).create(data)
        if "create" in aux:
            return self._aux_handler(resource).create(data)
        self._refuse(resource, "create")

    def _update(self, resource, fhir_id, data, partial):
        if self._is_aux_id(fhir_id):
            if "update" not in aux_interactions(resource):
                self._refuse(resource, "update")
            return self._aux_handler(resource).update(fhir_id, data, partial=partial)
        if "update" not in mapped_interactions(resource):
            self._refuse(resource, "update")
        # Dispatched to the handler, which raises MethodNotAllowed itself (MappedResourceHandler's
        # default) unless it overrides update -- only ObservationHandler currently does.
        return self._mapped_handler(resource).update(fhir_id, data, partial=partial)

    def _destroy(self, resource, fhir_id):
        if self._is_aux_id(fhir_id):
            if "delete" not in aux_interactions(resource):
                self._refuse(resource, "delete")
            return self._aux_handler(resource).delete(fhir_id)
        if "delete" not in mapped_interactions(resource):
            self._refuse(resource, "delete")
        # Dispatched to the handler, which raises MethodNotAllowed itself (MappedResourceHandler's
        # default) unless it overrides delete -- only ObservationHandler currently does.
        return self._mapped_handler(resource).delete(fhir_id)

    # -- search: exactly one backing store, chosen by _source --

    def _resolve_source_target(self, resource):
        """Pick the single backing store for a search from the ``_source`` param.

        Returns ``("mapped", None)``, ``("aux", fhir_source_id_or_None)``, or ``("empty", None)``:

          * ``_source:below=<fhir-source-base>/`` (or without the trailing slash) -> every imported
            aux row (``aux``, no source filter). Any other ``:below`` prefix -> ``empty``.
          * ``_source`` absent -> the mapped rows for a mapped type, else the aux rows (a pure-aux
            type has no mapped store).
          * ``_source`` == the JHE-native URI -> the mapped rows.
          * ``_source`` == ``<fhir-source-base>/<id>`` -> that one source's aux rows.
          * any other ``_source`` (an external URI, a typo) -> ``empty`` (matches nothing, since
            every stored ``meta.source`` is either the JHE-native URI or a ``<base>/<id>`` URI).

        The ``X-JHE-FHIR-Source-ID`` header plays no part here -- it is write-only.
        """
        below = self.request.GET.get("_source:below")
        if below is not None:
            return ("aux", None) if below.rstrip("/") == JHE_FHIR_SOURCE_BASE else ("empty", None)
        source = self.request.GET.get("_source")
        if source is None:
            return ("mapped", None) if is_mapped_resource(resource) else ("aux", None)
        if source == JHE_NATIVE_SOURCE:
            return ("mapped", None)
        fhir_source_id = parse_fhir_source_id(source)
        return ("aux", fhir_source_id) if fhir_source_id is not None else ("empty", None)

    def _search_source(self, resource):
        """Resolve a search to a single ``(queryset, serialize_fn, store)`` per ``_resolve_source_target``.

        ``store`` is ``"mapped"``, ``"aux"`` or ``"empty"``. A store the type does not expose (a
        mapped target on a pure-aux type, an unrecognized ``_source``, or a store whose ``search``
        interaction is not allowed) yields an empty queryset with store ``"empty"`` -- its
        ``serialize_fn`` is never called and no resource-specific filters are applied.
        """
        target, fhir_source_id = self._resolve_source_target(resource)
        if target == "mapped" and is_mapped_resource(resource) and "search" in mapped_interactions(resource):
            handler = self._mapped_handler(resource)
            return handler.search(), handler.serialize, "mapped"
        if target == "aux" and is_aux_resource(resource) and "search" in aux_interactions(resource):
            handler = self._aux_handler(resource)
            return handler.search(fhir_source_id=fhir_source_id), handler.serialize, "aux"
        return FhirAuxResource.objects.none(), (lambda obj: obj), "empty"

    def _search_bundle(self, resource):
        queryset, serialize, store = self._search_source(resource)
        queryset = apply_common_search_filters(queryset, self.request)
        if store != "empty":
            queryset = apply_search_params(queryset, resource, self.request, store)
        if summary_count_requested(self.request):
            return self._count_bundle(queryset)
        paginator = FHIRBundlePagination()
        page = paginator.paginate_queryset(queryset, self.request, view=self)
        entries = [{"resource": serialize(obj)} for obj in page]
        return paginator.get_paginated_response(entries)

    def _count_bundle(self, queryset):
        # _summary=count: the searchset total only, no entries (FHIR count summary).
        return Response(
            {
                "resourceType": "Bundle",
                "type": "searchset",
                "total": queryset.count(),
                "entry": [],
                "link": [{"relation": "self", "url": self.request.build_absolute_uri()}],
                "meta": {},
            }
        )

    # -- error rendering --

    def handle_exception(self, exc):
        """Render domain/model exceptions as FHIR OperationOutcome with the right status."""
        if isinstance(exc, (DjangoPermissionDenied, DRFPermissionDenied)):
            return self._outcome(http_status.HTTP_403_FORBIDDEN, exc)
        if isinstance(exc, NotFound):
            return self._outcome(http_status.HTTP_404_NOT_FOUND, exc)
        if isinstance(exc, MethodNotAllowed):
            return self._outcome(http_status.HTTP_405_METHOD_NOT_ALLOWED, exc)
        if isinstance(exc, IntegrityError):
            return self._outcome(http_status.HTTP_409_CONFLICT, exc)
        if isinstance(exc, (DjangoBadRequest, DRFValidationError)):
            return self._outcome(http_status.HTTP_400_BAD_REQUEST, exc)
        if isinstance(exc, APIException):
            status_code = exc.status_code if isinstance(exc.status_code, int) else http_status.HTTP_400_BAD_REQUEST
            return self._outcome(status_code, exc)
        logger.exception("Unhandled error in FHIR resource view")
        return self._outcome(http_status.HTTP_422_UNPROCESSABLE_ENTITY, exc)

    @staticmethod
    def _outcome(status_code, exc):
        # An exception may already carry a whole OperationOutcome as its detail (a duplicate
        # create does, so its issue keeps the FHIR `duplicate` code rather than the generic
        # `processing` one); render that as-is instead of stringifying it.
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict) and detail.get("resourceType") == "OperationOutcome":
            return Response(detail, status=status_code)
        return Response(FHIRBase.error_outcome(str(exc)), status=status_code)


def _camelized(data):
    import humps

    return humps.camelize(data)


@api_view(["GET", "HEAD"])
@permission_classes([AllowAny])
@renderer_classes(FHIR_RENDERER_CLASSES)
@cache_control(public=True, max_age=3600)
def capability_statement(request):
    """GET /FHIR/R5/metadata -- the public capabilities interaction (no auth, per FHIR)."""
    return Response(build_capability_statement(request))


@api_view(["GET", "HEAD"])
@permission_classes([AllowAny])
@renderer_classes([JSONRenderer])  # NOT the camel-case renderer: SMART keys are snake_case by spec
@cache_control(public=True, max_age=3600)
def smart_configuration(request):
    """GET /FHIR/R5/.well-known/smart-configuration -- SMART App Launch discovery.

    SMART App Launch requires this document at the FHIR base; it is what
    public (PKCE) browser clients fetch to find the OAuth endpoints. The
    capability list is deliberately minimal-honest: standalone launch with
    public or confidential clients over the authorization-code + PKCE flow.
    """
    return Response(
        {
            "authorization_endpoint": authorize_uri(request),
            "token_endpoint": token_uri(request),
            "grant_types_supported": ["authorization_code"],
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "capabilities": ["launch-standalone", "client-public", "client-confidential-symmetric"],
        }
    )
