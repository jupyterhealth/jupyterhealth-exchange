"""R4 ingestion endpoint.

``POST /fhir-import/R4/<resource>`` accepts a single R4 FHIR resource; ``POST /fhir-import/R4``
accepts a Bundle of R4 resources. Each body is converted to R5 by the cross-version engine
(:mod:`core.fhir.cross_version`) and then handed to the *normal* create routing on
:class:`~core.views.fhir.FHIRResourceView` -- so mapped-vs-aux routing, R5 validation, the
``X-JHE-FHIR-Source-ID`` write context, and JHE provenance stamping are all reused unchanged.

Because this is an **import** (a lossy R4 -> R5 conversion), not a pure FHIR create, *both*
endpoints always return a ``batch-response`` **Bundle**, and every entry carries an
``OperationOutcome`` at ``response.outcome``: warnings naming any R4 fields dropped in conversion,
an "informational, no loss" note when nothing was dropped, or the error when an entry failed. The
conversion is best-effort and lossy; R5 validation on the create path is the gate that rejects
anything the transform could not produce cleanly.
"""

import logging

from rest_framework import status as http_status
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from core.fhir.cross_version import XVerError, dropped_field_paths, transform_to_r5

from .fhir import FHIRResourceView, _camelized, resolve_fhir_source_context

logger = logging.getLogger(__name__)


class FHIRImportView(FHIRResourceView):
    """Create-only R4 endpoint: convert R4 -> R5, then delegate to the normal create routing,
    returning a batch-response Bundle with a per-entry OperationOutcome."""

    # Import is create-only; the inherited read/update/delete verbs are refused.
    def get(self, request, *args, **kwargs):
        raise MethodNotAllowed("GET", detail="The R4 import endpoint only supports create (POST).")

    def put(self, request, *args, **kwargs):
        raise MethodNotAllowed("PUT", detail="The R4 import endpoint only supports create (POST).")

    def patch(self, request, *args, **kwargs):
        raise MethodNotAllowed("PATCH", detail="The R4 import endpoint only supports create (POST).")

    def delete(self, request, *args, **kwargs):
        raise MethodNotAllowed("DELETE", detail="The R4 import endpoint only supports create (POST).")

    def post(self, request, resource=None, id=None):
        if resource is not None and id is not None:
            raise MethodNotAllowed("POST")
        # One source header gates the whole request, so resolve it up front: a missing/unknown/
        # forbidden source is a request-level 400/403, not a per-entry outcome.
        resolve_fhir_source_context(request, request.user)
        if resource is None:
            entries = self._process_bundle(request.data)
        else:
            entries = [self._process_resource(resource, request.data)]
        return Response(
            {"resourceType": "Bundle", "type": "batch-response", "entry": entries},
            status=http_status.HTTP_200_OK,
        )

    # -- per-entry processing --

    def _process_bundle(self, data):
        bundle = _camelized(data)
        if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
            raise DRFValidationError("Expected a Bundle at /fhir-import/R4.")
        entries = []
        for entry in bundle.get("entry", []) or []:
            resource = (entry or {}).get("resource") or {}
            entries.append(self._process_resource(resource.get("resourceType"), resource, already_camel=True))
        return entries

    def _process_resource(self, resource_type, body, already_camel=False):
        """Convert one R4 resource and create it; return a Bundle entry (success or error)."""
        dropped = []
        try:
            if not resource_type:
                raise DRFValidationError("Bundle entry is missing a resource / resourceType.")
            self._check_supported(resource_type)
            camel = body if already_camel else _camelized(body)
            r5 = self._convert(resource_type, camel)
            dropped = dropped_field_paths(camel, r5)
            if dropped:
                logger.warning(
                    "cross_version import: %s dropped R4 fields with no R5 home: %s",
                    resource_type,
                    ", ".join(dropped),
                )
            created = self._create(resource_type, r5)
            return _success_entry(created, resource_type, dropped)
        except Exception as exc:  # per-entry best-effort, mirroring batch semantics.
            # Include any drops even on failure: a dropped *required* field is usually *why* the
            # create failed (e.g. "medication field required" alongside "medication was dropped").
            return _error_entry(exc, resource_type, dropped)

    def _convert(self, resource_type, camel_body):
        try:
            return transform_to_r5(resource_type, camel_body)
        except XVerError as exc:
            raise DRFValidationError(str(exc))


def _success_entry(created, resource_type, dropped):
    return {
        "response": {"status": "201 Created", "outcome": _outcome(resource_type, dropped)},
        "resource": created,
    }


def _dropped_issues(resource_type, dropped):
    return [
        {
            "severity": "warning",
            "code": "not-supported",
            "diagnostics": f"R4 field '{resource_type}.{path}' has no R5 equivalent and was dropped in conversion.",
            "expression": [f"{resource_type}.{path}"],
        }
        for path in (dropped or [])
    ]


def _outcome(resource_type, dropped):
    """An OperationOutcome: one warning per dropped R4 path, or an informational "no loss" note."""
    issues = _dropped_issues(resource_type, dropped) or [
        {
            "severity": "information",
            "code": "informational",
            "diagnostics": "Converted R4 -> R5 with no detected field loss.",
        }
    ]
    return {"resourceType": "OperationOutcome", "issue": issues}


def _error_entry(exc, resource_type=None, dropped=None):
    detail = getattr(exc, "detail", None) or str(exc)
    code = getattr(exc, "status_code", http_status.HTTP_400_BAD_REQUEST)
    # The error first, then any dropped-field warnings (a dropped required field is often the cause).
    issues = [{"severity": "error", "code": "processing", "diagnostics": str(detail)}]
    issues += _dropped_issues(resource_type, dropped)
    return {
        "response": {
            "status": f"{code} {getattr(exc, 'default_code', 'error')}",
            "outcome": {"resourceType": "OperationOutcome", "issue": issues},
        }
    }
