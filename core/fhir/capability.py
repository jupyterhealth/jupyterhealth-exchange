"""Render the server's CapabilityStatement from the FHIR mapping config.

GET /FHIR/R5/metadata is public (FHIR requires the capabilities interaction to
be retrievable) and exposes only resource-type names, interaction codes, and
search-param names -- all already public in fhir_config.json. The statement
follows the US Core server CapabilityStatement conventions (expectation
extensions, SMART security block) WITHOUT claiming US Core conformance: US
Core targets R4 and requires capabilities JHE does not serve (vread/history,
Provenance _revinclude, US Core profiles).

Dual-store honesty: a resource can be backed by a Django model (JHE-native)
and/or the FhirAuxResource store (imported records, selected with ``_source``).
Declared interactions and search params are the union of both stores, with
``documentation`` naming the caveats (which writes reach which store, which
params only apply under ``_source``), so a generic client is never silently
misled.
"""

from django.utils import timezone

from core.auth import authorize_uri, token_uri
from core.fhir.config import (
    FHIR_VERSION,
    aux_interactions,
    aux_search_params,
    is_mapped_resource,
    mapped_interactions,
    mapped_search_params,
    supported_resource_types,
)

_EXPECTATION_URL = "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation"
_OAUTH_URIS_URL = "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris"
_SECURITY_SERVICE_SYSTEM = "http://terminology.hl7.org/CodeSystem/restful-security-service"
# Strict map: an unmapped FHIR_VERSION must fail loudly (KeyError at import of
# the statement), not silently emit an invalid CapabilityStatement.fhirVersion.
_FHIR_VERSION_NUMBERS = {"R5": "5.0.0"}
# Config interaction name -> FHIR RESTful interaction code (identity when absent).
_INTERACTION_CODES = {"search": "search-type"}
# Config __search value type -> FHIR search-param type (identity when absent).
_SEARCH_TYPES = {"identifier": "token", "code": "token", "const": "token"}
# Interactions the mapped (Django-model) handler actually implements: model-side
# update/delete unconditionally 405 in core/views/fhir.py regardless of the
# config allow-list, so they must not be declared on the mapped store's behalf.
_MAPPED_IMPLEMENTED = frozenset({"create", "read", "search"})
# Params resolved by the canonical patient-scoped filters (core/views/fhir.py
# _canonical_search_kwargs) on EVERY search, whichever store backs it -- these
# are never "aux-only" even when only the aux __search block declares them.
_CANONICAL_PARAMS = frozenset({"identifier", "code"})
# Resource-agnostic params served by apply_common_search_filters / the view.
_COMMON_SEARCH_PARAMS = (
    ("_id", "token", None),
    ("_lastUpdated", "date", None),
    (
        "_source",
        "uri",
        "Selects the backing store: omit (or pass the JHE-native source URI) for "
        "JHE-native records; pass an imported FhirSource URI (or _source:below) "
        "for imported records.",
    ),
)
# The statement describes capabilities fixed at deploy time, so its date is the
# process start, not the request time -- keeps the response byte-stable and
# cacheable between deploys.
_BUILD_DATE = timezone.now().isoformat()


def _shall(element):
    """Attach the US Core expectation extension: everything declared is supported."""
    element["extension"] = [{"url": _EXPECTATION_URL, "valueCode": "SHALL"}]
    return element


def _codes(interactions):
    return sorted(_INTERACTION_CODES.get(i, i) for i in interactions)


def _resource_entry(resource_type):
    mapped = mapped_interactions(resource_type) & _MAPPED_IMPLEMENTED
    aux = aux_interactions(resource_type)
    interactions = mapped | aux
    if "update" in aux:
        interactions = interactions | {"patch"}  # the aux store serves PATCH as a JSON merge
    entry = {"type": resource_type}

    docs = []
    aux_only_writes = aux - mapped - {"read", "search"}
    if mapped and aux_only_writes:
        docs.append(
            f"The {', '.join(_codes(aux_only_writes | {'patch'} if 'update' in aux_only_writes else aux_only_writes))} "
            f"interactions apply to imported records only; JHE-native records support {', '.join(_codes(mapped))}."
        )
    if "create" in aux:
        docs.append(
            "Creates routed to the imported-record store must name a FhirSource via the "
            "X-JHE-FHIR-Source-ID header or the resource's meta.source."
        )
    if resource_type == "Observation":
        # The payload encoding is a profile-level fact with no StructureDefinition
        # to point supportedProfile at (yet), so it is stated as documentation.
        docs.append(
            "JHE-native Observations code their measurement with system "
            "https://w3id.org/openmhealth and carry the full Open mHealth data "
            "point as a base64 JSON valueAttachment (contentType application/json)."
        )
    if docs:
        entry["documentation"] = " ".join(docs)

    if interactions:
        entry["interaction"] = [_shall({"code": code}) for code in _codes(interactions)]

    mapped_params = mapped_search_params(resource_type)
    aux_params = aux_search_params(resource_type)
    # Union of both stores' params: a search hits one store, but the client
    # cannot choose per-param, so "supported" is the union. Mapped specs win ties.
    merged = {**aux_params, **mapped_params}
    search_params = []
    for name, spec in sorted(merged.items()):
        param = {"name": name, "type": _SEARCH_TYPES.get(spec["type"], spec["type"])}
        aux_only = is_mapped_resource(resource_type) and name not in mapped_params and name not in _CANONICAL_PARAMS
        if aux_only:
            param["documentation"] = (
                "Applies when searching imported records via _source; the default JHE-native search ignores it."
            )
        search_params.append(_shall(param))
    for name, param_type, documentation in _COMMON_SEARCH_PARAMS:
        param = {"name": name, "type": param_type}
        if documentation:
            param["documentation"] = documentation
        search_params.append(_shall(param))
    entry["searchParam"] = search_params
    return entry


def build_capability_statement(request):
    """The server's CapabilityStatement (R5, kind=instance), derived from fhir_config.json.

    Absolute URIs come from the request. Safe because ALLOWED_HOSTS bounds the
    Host header; if a deployment ever sets ALLOWED_HOSTS=* alongside
    USE_X_FORWARDED_HOST, the advertised OAuth URIs become attacker-suppliable
    per request -- keep ALLOWED_HOSTS explicit.
    """
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": _BUILD_DATE,
        "kind": "instance",
        "software": {"name": "JupyterHealth Exchange"},
        "implementation": {
            "description": "JupyterHealth Exchange",
            "url": request.build_absolute_uri(f"/FHIR/{FHIR_VERSION}/"),
        },
        "fhirVersion": _FHIR_VERSION_NUMBERS[FHIR_VERSION],
        "format": ["json"],
        "patchFormat": ["application/json"],
        "rest": [
            {
                "mode": "server",
                "documentation": (
                    "Searches also support _sort (date, lastUpdated) and _summary=count. "
                    "POST [base]/[type]/_search is accepted with form-encoded parameters."
                ),
                "security": {
                    "extension": [
                        {
                            "url": _OAUTH_URIS_URL,
                            "extension": [
                                {"url": "authorize", "valueUri": authorize_uri(request)},
                                {"url": "token", "valueUri": token_uri(request)},
                            ],
                        }
                    ],
                    "service": [{"coding": [{"system": _SECURITY_SERVICE_SYSTEM, "code": "SMART-on-FHIR"}]}],
                    "description": (
                        "OAuth 2.0 (SMART-on-FHIR). Unauthorized requests are rejected with 401, 403, or 404."
                    ),
                },
                "interaction": [_shall({"code": "batch"})],
                "resource": [_resource_entry(rt) for rt in supported_resource_types()],
            }
        ],
    }
