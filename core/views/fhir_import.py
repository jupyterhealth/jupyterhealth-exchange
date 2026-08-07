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

from .fhir import (
    FHIR_SOURCE_ID_HEADER,
    FHIRResourceView,
    _camelized,
    _source_id_from_body,
    resolve_fhir_source_context,
)

logger = logging.getLogger(__name__)


def _names_a_source(data):
    """True when the posted body (single resource or Bundle entries) carries a parseable meta.source."""
    body = data if isinstance(data, dict) else {}
    if body.get("resourceType") == "Bundle" or "entry" in body:
        return any(
            _source_id_from_body(entry.get("resource")) for entry in body.get("entry") or [] if isinstance(entry, dict)
        )
    return _source_id_from_body(body) is not None


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
        # A present source header gates the whole request up front (an unknown/forbidden
        # source fails fast as a request-level 400/403). Without the header, bodies may name
        # their own meta.source — resolution then happens per entry in the create path, so
        # the error text's "or set the resource's meta.source" holds for bundles too. Only
        # when nothing names a source anywhere is the request-level 400 raised.
        if request.headers.get(FHIR_SOURCE_ID_HEADER) or not _names_a_source(request.data):
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
            # A malformed entry (string, list, non-dict resource) must fail as ITS entry, not
            # abort the bundle -- earlier entries are already committed (no ATOMIC_REQUESTS),
            # so a whole-request error would misreport what was actually written.
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(resource, dict):
                entries.append(
                    _error_entry(DRFValidationError("Bundle entry is not an object with a 'resource' object."))
                )
                continue
            entries.append(self._process_resource(resource.get("resourceType"), resource, already_camel=True))
        return entries

    def _process_resource(self, resource_type, body, already_camel=False):
        """Convert one R4 resource and create it; return a Bundle entry (success or error)."""
        dropped = []
        enriched = []
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
            enriched = _enrich_r5(resource_type, r5)
            created = self._create(resource_type, r5)
            return _success_entry(created, resource_type, dropped, enriched)
        except Exception as exc:  # per-entry best-effort, mirroring batch semantics.
            # Include any drops and enrichments even on failure: a dropped *required* field is
            # usually *why* the create failed, and a defaulted field narrows what was tried.
            return _error_entry(exc, resource_type, dropped, enriched)

    def _convert(self, resource_type, camel_body):
        try:
            return transform_to_r5(resource_type, camel_body)
        except XVerError as exc:
            raise DRFValidationError(str(exc))


def _enrich_r5(resource_type, r5):
    """Default R5-mandatory fields that are optional in R4 and absent at the source.

    Returns the warning issues describing what was defaulted (empty when nothing was).
    Today: R4 Condition.clinicalStatus is 0..1 but R5 requires it; Epic emits many
    encounter-diagnosis/health-concern Conditions without one. Rather than reject the
    record, default to the value-set's own escape hatch 'unknown' and say so per record.
    """
    if resource_type == "Condition" and not r5.get("clinicalStatus"):
        r5["clinicalStatus"] = {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "unknown"}]
        }
        return [
            {
                "severity": "warning",
                "code": "informational",
                "diagnostics": (
                    "clinicalStatus was absent in the R4 source (optional in R4, required in R5); "
                    "defaulted to 'unknown'."
                ),
                "expression": ["Condition.clinicalStatus"],
            }
        ]
    if resource_type == "Device" and r5.get("udiCarrier"):
        # R4 Device.udiCarrier.issuer is 0..1; R5 requires it (1..1). Epic can emit carriers
        # with only deviceIdentifier/carrierHRF. No issuer can honestly be invented, so drop
        # the incomplete carrier entries (per record, with a warning) rather than reject the
        # whole Device.
        kept = [carrier for carrier in r5["udiCarrier"] if isinstance(carrier, dict) and carrier.get("issuer")]
        dropped_count = len(r5["udiCarrier"]) - len(kept)
        if dropped_count:
            if kept:
                r5["udiCarrier"] = kept
            else:
                del r5["udiCarrier"]
            return [
                {
                    "severity": "warning",
                    "code": "informational",
                    "diagnostics": (
                        f"{dropped_count} udiCarrier entr{'y' if dropped_count == 1 else 'ies'} had no issuer "
                        "(optional in R4, required in R5) and were dropped; the Device itself was kept."
                    ),
                    "expression": ["Device.udiCarrier"],
                }
            ]
    return []


def _success_entry(created, resource_type, dropped, enriched=None):
    return {
        "response": {"status": "201 Created", "outcome": _outcome(resource_type, dropped, enriched)},
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


def _outcome(resource_type, dropped, enriched=None):
    """An OperationOutcome: enrichment + dropped-field warnings, or an informational "no loss" note."""
    issues = (enriched or []) + _dropped_issues(resource_type, dropped)
    issues = issues or [
        {
            "severity": "information",
            "code": "informational",
            "diagnostics": "Converted R4 -> R5 with no detected field loss.",
        }
    ]
    return {"resourceType": "OperationOutcome", "issue": issues}


def _error_entry(exc, resource_type=None, dropped=None, enriched=None):
    detail = getattr(exc, "detail", None) or str(exc)
    code = getattr(exc, "status_code", http_status.HTTP_400_BAD_REQUEST)
    # The error first, then enrichment + dropped-field warnings (a dropped required field is
    # often the cause, and a defaulted field narrows what was attempted).
    issues = [{"severity": "error", "code": "processing", "diagnostics": str(detail)}]
    issues += enriched or []
    issues += _dropped_issues(resource_type, dropped)
    return {
        "response": {
            "status": f"{code} {getattr(exc, 'default_code', 'error')}",
            "outcome": {"resourceType": "OperationOutcome", "issue": issues},
        }
    }
