"""Unified FHIR R5 resource endpoint.

A single view, ``FHIRResourceView``, serves every supported FHIR resource at
``FHIR/<version>/<resource>`` (and ``.../<resource>/<id>``), where ``<version>`` comes
from core/fhir/fhir_config.json (e.g. ``FHIR/R5/Patient``); the lowercase ``fhir/r5/``
path is kept as a backward-compatible alias.

Routing is config-driven (see fhir_engine.md). A Django model backs only the JHE-system
view of each resource; everything else lives in the generic ``FhirAuxResource`` store:

  * **search** of a mapped type returns the UNION of the Django-mapped rows and the
    FhirAuxResource rows of that type.
  * **read / update / delete** are routed by id shape -- a UUID id targets FhirAuxResource,
    an integer id targets the mapped Django model.
  * **create** is routed by the resource's ``__interaction`` allow-list and, where present,
    its ``__criteria``: only an OMH Observation writes to the Django model; every other
    write lands in FhirAuxResource (linked to a FhirSource named by the X-JHE-FHIR-Source-ID header).

The FHIR bundle batch endpoint (POST at the base, e.g. ``FHIR/R5/``) remains served by FHIRBase.
"""

import logging
import uuid

from django.core.exceptions import BadRequest as DjangoBadRequest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.utils import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status as http_status
from rest_framework.exceptions import APIException, MethodNotAllowed, NotFound
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

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
from core.fhir.fhir_validation import validate_fhir_resource
from core.fhir.pagination import ConcatenatedResults, FHIRBundlePagination
from core.models import (
    FhirAuxResource,
    FhirSource,
    Observation,
    Patient,
    apply_jhe_extensions,
)
from core.serializers import FHIRAuxResourceSerializer, FHIRObservationSerializer
from core.views.fhir_base import FHIRBase

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
        system, _, value = code.partition("|")
        kwargs["coding_system"] = system
        kwargs["coding_code"] = value
    return kwargs


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
    ``ObservationHandler`` subclasses this to override serialization (Base64) and create.
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


class ObservationHandler(MappedResourceHandler):
    """Observation needs a custom serializer (Base64 valueAttachment) and the OMH create path."""

    def serialize(self, instance):
        # valueAttachment.data needs Base64 encoding, which the config can't express.
        return FHIRObservationSerializer().to_representation(instance)

    def create(self, data):
        # Only the OMH path reaches here (the view routes non-OMH Observations to aux).
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

    def _write_context(self):
        # Writes must name a source: (patient, fhir_source). Raises 400 if the header is
        # missing, 403/400 if the source is unknown or the user may not use it.
        return resolve_fhir_source_context(self.request, self.user)

    def _read_scope(self):
        # Reads go through the normalized FhirAuxResource.fhir_search (patient-vs-practitioner +
        # org-membership authorization), exactly like the mapped handler. The header wins: when
        # present it scopes the read to that source's patient (resolving the source also
        # authorizes the caller) and the canonical query params are ignored. When absent, the
        # canonical patient / patient.organization / patient._has:Group:member filters apply, so
        # e.g. ?patient._has:Group:member:_id=<study> returns the aux rows of that resource type
        # for the study's patients the practitioner can access.
        if self.request.headers.get(FHIR_SOURCE_ID_HEADER):
            patient, _ = resolve_fhir_source_context(self.request, self.user)
            return {"patient_id": patient.id}
        return _canonical_search_kwargs(self.request)

    def serialize(self, instance):
        return FHIRAuxResourceSerializer().to_representation(instance)

    def search(self):
        return FhirAuxResource.fhir_search(self.user.id, self.resource_type, **self._read_scope())

    def read(self, fhir_id):
        instance = FhirAuxResource.fhir_search(
            self.user.id, self.resource_type, resource_id=fhir_id, **self._read_scope()
        ).first()
        if instance is None:
            raise NotFound(f"{self.resource_type}/{fhir_id} not found.")
        return self.serialize(instance)

    def create(self, data):
        # The camel-case parser snake-cases incoming JSON; restore FHIR camelCase before
        # validating and storing so fhir_data round-trips as valid FHIR.
        data = _camelized(data)
        validate_fhir_resource(self.resource_type, data)
        _, fhir_source = self._write_context()
        return self.serialize(create_aux_resource(self.resource_type, data, fhir_source))

    def update(self, fhir_id, data, partial=False):
        patient, fhir_source = self._write_context()
        instance = self._write_instance(fhir_id, patient)
        body = _aux_body(_camelized(data))
        if partial:
            merged = dict(instance.fhir_data or {})
            merged.update(body)
            body = merged
        validate_fhir_resource(self.resource_type, {**body, "resourceType": self.resource_type})
        return self.serialize(_persist_aux(instance, self.resource_type, body, fhir_source))

    def delete(self, fhir_id):
        patient, _ = self._write_context()
        self._write_instance(fhir_id, patient).delete()

    def _write_instance(self, fhir_id, patient):
        # A write targets a record under the named source's patient.
        try:
            return FhirAuxResource.for_patient(patient, self.resource_type).get(pk=fhir_id)
        except (FhirAuxResource.DoesNotExist, ValueError, TypeError):
            raise NotFound(f"{self.resource_type}/{fhir_id} not found.")


def resolve_fhir_source_context(request, user):
    """Resolve ``(patient, fhir_source)`` from the ``X-JHE-FHIR-Source-ID`` header.

    The patient is taken from the user's own access token (patient users) or from the source's
    patient (non-patient users). Missing header -> 400; unknown source -> 400; a source the user
    may not use -> 403.
    """
    source_id = request.headers.get(FHIR_SOURCE_ID_HEADER)
    if not source_id:
        raise DRFValidationError(f"Header '{FHIR_SOURCE_ID_HEADER}' is required to write this resource.")
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

    # Non-patient (practitioner): derive the patient from the source, with org-sharing authz.
    if not Patient.practitioner_authorized(user.id, fhir_source.patient_id):
        raise DRFPermissionDenied("Current user does not have access to this FhirSource's patient.")
    return fhir_source.patient, fhir_source


def _aux_body(data):
    # Store the FHIR body verbatim minus resourceType (it is derived from the column).
    return {key: value for key, value in dict(data).items() if key != "resourceType"}


def _derive_patient_fhir_id(resource_type, body):
    # Best-effort extraction of the resource's referenced Patient id (may be None).
    if resource_type == "Patient":
        return body.get("id")
    for key in ("subject", "patient", "beneficiary"):
        node = body.get(key)
        reference = node.get("reference") if isinstance(node, dict) else None
        if isinstance(reference, str) and reference.startswith("Patient/"):
            return reference.split("/", 1)[1]
    return None


def _persist_aux(instance, resource_type, body, fhir_source):
    body = apply_jhe_extensions(_aux_body(body), fhir_source)
    instance.resource_type = resource_type
    instance.fhir_source = fhir_source
    instance.patient_fhir_id = _derive_patient_fhir_id(resource_type, body)
    instance.fhir_resource_id = body.get("id")
    instance.fhir_data = body
    instance.save()
    return instance


def create_aux_resource(resource_type, data, fhir_source):
    """Create a FhirAuxResource of ``resource_type`` linked to ``fhir_source`` (and its patient)."""
    return _persist_aux(FhirAuxResource(), resource_type, _aux_body(data), fhir_source)


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
            self._remember_resource(resource)
            return self._search_bundle(resource)
        return Response(self._read(resource, id))

    def _remember_resource(self, resource):
        # Make the admin UI's Resource select sticky, mirroring how the studies/observations
        # viewsets persist current_organization_id / current_study_id (see core/views/study.py).
        user = self.request.user
        if hasattr(user, "practitioner_profile"):
            user.practitioner_profile.save_setting("current_fhir_resource", resource)

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
        # OMH criteria routes a writable mapped resource between the model and aux.
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
        # No mapped resource currently implements model-side update.
        raise MethodNotAllowed("PUT", detail=f"Update of a {resource} record is not supported.")

    def _destroy(self, resource, fhir_id):
        if self._is_aux_id(fhir_id):
            if "delete" not in aux_interactions(resource):
                self._refuse(resource, "delete")
            return self._aux_handler(resource).delete(fhir_id)
        if "delete" not in mapped_interactions(resource):
            self._refuse(resource, "delete")
        raise MethodNotAllowed("DELETE", detail=f"Delete of a {resource} record is not supported.")

    # -- search: union of mapped rows + aux rows --

    def _search_bundle(self, resource):
        # Union of the mapped Django rows and the FhirAuxResource rows, mapped first. Each source
        # is a (queryset, serialize_fn) pair so the paginator slices it at the DB level.
        sources = []
        if is_mapped_resource(resource) and "search" in mapped_interactions(resource):
            handler = self._mapped_handler(resource)
            sources.append((apply_common_search_filters(handler.search(), self.request), handler.serialize))
        if is_aux_resource(resource) and "search" in aux_interactions(resource):
            # Reads don't require the source header: the aux handler scopes to the source's
            # patient when it is present, else to everything the user can access.
            handler = self._aux_handler(resource)
            sources.append((apply_common_search_filters(handler.search(), self.request), handler.serialize))

        paginator = FHIRBundlePagination()
        page = paginator.paginate_queryset(ConcatenatedResults(sources), self.request, view=self)
        entries = [{"resource": serialize(obj)} for serialize, obj in page]
        return paginator.get_paginated_response(entries)

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
        return Response(FHIRBase.error_outcome(str(exc)), status=status_code)


def _camelized(data):
    import humps

    return humps.camelize(data)
