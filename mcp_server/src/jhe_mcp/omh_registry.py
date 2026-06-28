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


def lookup_code(data_type_short_name: str) -> str | None:
    for sid in all_schema_ids():
        if short_name(sid) == data_type_short_name:
            return f"{_coding_system(sid)}|{sid}"
    return None


def all_short_names() -> list[str]:
    return sorted(short_name(sid) for sid in all_schema_ids())
