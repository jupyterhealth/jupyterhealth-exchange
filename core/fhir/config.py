"""Loads and validates the FHIR mapping configuration.

core/fhir/fhir_config.json declares two arrays of resource entries:

  * ``mapped_resources`` -- resources projected onto a dedicated Django model via a field
    mapping (Observation->Observation, Device->DataSource, Group->Study, Organization,
    Patient, Practitioner). Each carries an ``__interaction`` allow-list and, for resources
    whose interactions cover everything, a ``__criteria`` predicate that routes a write
    between the Django model and the generic FhirAuxResource overflow store.
  * ``aux_resources`` -- every resource type that can be stored opaquely in FhirAuxResource,
    each with its own ``__interaction`` allow-list. Mapped types appear here too (their
    non-model rows fall through to FhirAuxResource).

The JSON is read once at import time (no hot reload). It is *validated* lazily (the first
time ``get_config_errors()`` is called, once the Django app registry is ready) so a
malformed config surfaces as a 500 with a useful message rather than crashing startup.
"""

import importlib
import json
from functools import lru_cache
from pathlib import Path

from core.fhir.engine import (
    ALL_INTERACTIONS,
    INTERACTION_WILDCARD,
    expand_interactions,
    get_mapping_criteria,
    get_mapping_interactions,
)

_CONFIG_PATH = Path(__file__).resolve().parent / "fhir_config.json"

with open(_CONFIG_PATH) as f:
    FHIR_CONFIG = json.load(f)

FHIR_VERSION = FHIR_CONFIG.get("fhir_version")

# Mapped resources are indexed by resourceType with the resourceType key stripped, so the
# stored value is exactly the field mapping the engine walks. Auxiliary resources keep their
# whole entry (we need the __interaction list off it).
_MAPPED_RESOURCES = {
    entry["resourceType"]: {key: value for key, value in entry.items() if key != "resourceType"}
    for entry in FHIR_CONFIG.get("mapped_resources", [])
}
_AUX_RESOURCES = {entry["resourceType"]: entry for entry in FHIR_CONFIG.get("aux_resources", [])}


def get_resource_mapping(resource_type):
    """Return the field mapping declared for a mapped FHIR resource type (e.g. "Patient").

    Returns an empty dict when the resource type is not a mapped resource.
    """
    return _MAPPED_RESOURCES.get(resource_type, {})


def is_mapped_resource(resource_type):
    """True for resources backed by a Django model + field mapping (Patient, Group, ...)."""
    return resource_type in _MAPPED_RESOURCES


def is_aux_resource(resource_type):
    """True for resources that can be stored as opaque JSON blobs in FhirAuxResource."""
    return resource_type in _AUX_RESOURCES


def is_supported_resource(resource_type):
    """True if the resource type is served at all (either mapped or auxiliary)."""
    return is_mapped_resource(resource_type) or is_aux_resource(resource_type)


def aux_resource_types():
    """The set of supported auxiliary (JSON-blob) resource types."""
    return frozenset(_AUX_RESOURCES)


def supported_resource_types():
    """The sorted list of every resource type served (mapped and/or auxiliary)."""
    return sorted(set(_MAPPED_RESOURCES) | set(_AUX_RESOURCES))


def mapped_interactions(resource_type):
    """The set of interactions a *mapped* resource exposes against its Django model.

    A mapped resource declares its allow-list as ``meta.__interaction`` (located anywhere in
    the mapping), so the recursive ``get_mapping_interactions`` is used.
    """
    return expand_interactions(get_mapping_interactions(_MAPPED_RESOURCES.get(resource_type, {})))


def aux_interactions(resource_type):
    """The set of interactions a resource exposes against the FhirAuxResource overflow store.

    Auxiliary entries carry ``__interaction`` at the top level of the entry.
    """
    return expand_interactions(_AUX_RESOURCES.get(resource_type, {}).get("__interaction"))


def ref_paths_for(resource_type):
    """The reference paths declared for an auxiliary resource type (issue #584).

    Each is a dotted path into the FHIR body ending at a ``reference`` string (e.g.
    ``"subject.reference"``) that the index-refs pass rewrites from an upstream id to a JHE id.
    Returns an empty list when none are declared.
    """
    return _AUX_RESOURCES.get(resource_type, {}).get("__refPaths", [])


def mapped_criteria(resource_type):
    """The ``__criteria`` predicate declared for a mapped resource, or ``None``."""
    return get_mapping_criteria(_MAPPED_RESOURCES.get(resource_type, {}))


def mapped_model_name(resource_type):
    """The Django model name backing a mapped resource (the prefix of its path tokens).

    Every mapped resource declares ``"id": "<Model>.id"``, so the backing model is the first
    dotted segment of that path (e.g. ``Device`` -> ``DataSource``, ``Group`` -> ``Study``).
    """
    id_path = _MAPPED_RESOURCES.get(resource_type, {}).get("id", "")
    return id_path.split(".", 1)[0] if isinstance(id_path, str) and "." in id_path else None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_config_errors():
    """Validate the config once and cache the list of human-readable error messages.

    Returns an empty tuple when the config is valid. Called lazily by the FHIR view so a
    malformed config yields a 500 OperationOutcome (rather than crashing app startup, which
    would also break unrelated commands like migrations).
    """
    return tuple(validate_config())


def validate_config():
    """Validate fhir_config.json, returning a list of error strings (empty when valid)."""
    errors = []
    mapped_entries = FHIR_CONFIG.get("mapped_resources", [])
    aux_entries = FHIR_CONFIG.get("aux_resources", [])

    for entry in mapped_entries:
        # Mapped resources declare meta.__interaction (located recursively in the mapping).
        interactions = get_mapping_interactions({k: v for k, v in entry.items() if k != "resourceType"})
        _validate_interaction(entry.get("resourceType"), interactions, "mapped_resources", errors)
    for entry in aux_entries:
        _validate_interaction(entry.get("resourceType"), entry.get("__interaction"), "aux_resources", errors)

    for resource_type, mapping in _MAPPED_RESOURCES.items():
        # Rule 3: a mapped resource exposing every interaction would never fall back to the
        # FhirAuxResource overflow store, so it must declare a __criteria to route writes.
        interactions = mapped_interactions(resource_type)
        if interactions == set(ALL_INTERACTIONS) and mapped_criteria(resource_type) is None:
            errors.append(
                f"mapped_resources '{resource_type}': __interaction covers all interactions"
                " (or uses '*') but no __criteria is declared to route writes to FhirAuxResource."
            )
        # Rule 4: every path token must resolve on the backing Django model.
        errors.extend(_validate_paths(resource_type, mapping))
        # Rule 5: every (non-annotation) field name must be a valid FHIR element.
        errors.extend(_validate_fhir_fields(resource_type, mapping))

    return errors


def _validate_interaction(resource_type, interactions, section, errors):
    resource_type = resource_type or "<missing resourceType>"
    if not isinstance(interactions, list) or not interactions:
        errors.append(f"{section} '{resource_type}': __interaction must be a non-empty list.")
        return
    valid = set(ALL_INTERACTIONS) | {INTERACTION_WILDCARD}
    for interaction in interactions:
        if interaction not in valid:
            errors.append(
                f"{section} '{resource_type}': invalid interaction '{interaction}'"
                f" (allowed: {sorted(ALL_INTERACTIONS)} or '{INTERACTION_WILDCARD}')."
            )


# -- Rule 4: path resolution against the backing Django model --


def _iter_paths(node, source_model):
    """Yield every path token (``Model.field...``) appearing in a mapping node."""
    if isinstance(node, str):
        for token in node.split("+"):
            token = token.strip()
            if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
                continue  # literal
            if token.split(".", 1)[0] == source_model:
                yield token
    elif isinstance(node, dict):
        for key, child in node.items():
            if not (isinstance(key, str) and key.startswith("__")):
                yield from _iter_paths(child, source_model)
    elif isinstance(node, list):
        for child in node:
            yield from _iter_paths(child, source_model)


def _validate_paths(resource_type, mapping):
    from django.apps import apps

    model_name = mapped_model_name(resource_type)
    if not model_name:
        return [f"mapped_resources '{resource_type}': cannot determine backing model (no 'id' path)."]
    try:
        model_cls = apps.get_model("core", model_name)
    except LookupError:
        return [f"mapped_resources '{resource_type}': backing model 'core.{model_name}' does not exist."]

    errors = []
    for path in _iter_paths(mapping, model_name):
        problem = _resolve_path_attrs(model_cls, path.split(".")[1:])
        if problem:
            errors.append(f"mapped_resources '{resource_type}': path '{path}' is invalid -- {problem}.")
    return errors


def _resolve_path_attrs(model_cls, segments):
    """Return None if the attribute chain resolves on the model, else a reason string.

    Walks model fields and FK/relation hops; a ``@property`` or other class attribute is
    accepted as a terminal we cannot introspect further (e.g. ``codeable_concepts``).
    """
    cls = model_cls
    for seg in segments:
        if cls is None:
            return f"'{seg}' follows a non-relational field"
        try:
            field = cls._meta.get_field(seg)
        except Exception:
            field = None
        if field is not None:
            cls = field.related_model if field.is_relation else None
            continue
        if hasattr(cls, seg):
            cls = None  # property / descriptor: accept but cannot traverse further
            continue
        return f"'{seg}' is not a field or attribute of {cls.__name__}"
    return None


# -- Rule 5: FHIR element-name validation --


def _fhir_model_class(resource_type):
    module = importlib.import_module(f"fhir.resources.{resource_type.lower()}")
    return getattr(module, resource_type)


def _validate_fhir_fields(resource_type, mapping):
    try:
        model_cls = _fhir_model_class(resource_type)
    except (ImportError, AttributeError):
        return [f"mapped_resources '{resource_type}': not a valid FHIR {FHIR_VERSION} resource type."]

    valid = set(model_cls.elements_sequence())
    errors = []
    for key in mapping:
        if isinstance(key, str) and key.startswith("__"):
            continue
        if key not in valid:
            errors.append(
                f"mapped_resources '{resource_type}': '{key}' is not a valid FHIR element of {resource_type}."
            )
    return errors
