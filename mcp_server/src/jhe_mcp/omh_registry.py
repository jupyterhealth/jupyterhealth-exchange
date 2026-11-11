from __future__ import annotations

from typing import Any

from omh_shim import known_ids
from omh_shim import load_schema as _shim_load

_OMH_SYSTEM = "https://w3id.org/openmhealth"

# Coding system per schema-id namespace prefix, matching the systems JHE seeds
# in ``seed_codeable_concepts`` (e.g. ``ieee:sleep-stage-summary:1.0`` is seeded
# under the IEEE 1752 system, not the OMH one). Unknown prefixes fall back to the
# OMH system, preserving the prior behaviour for ``omh:`` and ``local:`` ids.
_CODING_SYSTEMS = {
    "omh": _OMH_SYSTEM,
    "ieee": "https://w3id.org/ieee1752",
}


def all_schema_ids() -> frozenset[str]:
    return known_ids()


def load_schema(schema_id: str) -> dict[str, Any]:
    return _shim_load(schema_id)


def short_name(schema_id: str) -> str:
    parts = schema_id.split(":")
    return parts[1] if len(parts) >= 2 else schema_id


def _coding_system(schema_id: str) -> str:
    prefix = schema_id.split(":", 1)[0]
    return _CODING_SYSTEMS.get(prefix, _OMH_SYSTEM)


def schema_ids_for(data_type: str) -> list[str]:
    # A full id resolves to itself; a short name is sorted so an ambiguous one is IEEE-first in every process
    if data_type in all_schema_ids():
        return [data_type]
    return sorted(sid for sid in all_schema_ids() if short_name(sid) == data_type)


def candidate_codes(data_type: str) -> list[str]:
    # One code per schema, IEEE-first, for the count probe in resolve_observation_params:
    # an ambiguous short name resolves to the one system that holds data, not the union of both.
    return [f"{_coding_system(sid)}|{sid}" for sid in schema_ids_for(data_type)]


def lookup_code(data_type: str) -> str | None:
    return next(iter(candidate_codes(data_type)), None)


def all_short_names() -> list[str]:
    return sorted({short_name(sid) for sid in all_schema_ids()})


def known_data_types() -> list[str]:
    # Short names, then the full ids of any short name that spans more than one system
    return all_short_names() + sorted(sid for sid in all_schema_ids() if len(schema_ids_for(short_name(sid))) > 1)
