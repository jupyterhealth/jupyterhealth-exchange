import re
from pathlib import Path

import pytest
from jhe_mcp.omh_registry import all_schema_ids, all_short_names, load_schema, lookup_code, short_name

# JHE's seed command, relative to this test: tests/unit -> tests -> mcp_server
# -> <repo root>. Present in any monorepo checkout (incl. CI, which checks out
# the full repo); absent only if the package is copied out standalone.
_SEED_PY = Path(__file__).resolve().parents[3] / "core" / "management" / "commands" / "seed.py"


def _jhe_seeded_short_names() -> set[str]:
    """Derive JHE's authoritative supported schema codes straight from seed.py.

    Parses the ``seed_codeable_concepts`` block of JHE's seed command rather than
    hardcoding the set, so when JHE adds a CodeableConcept the new code is picked
    up automatically and the coverage assertion fails until omh-shim vendors a
    schema for it. A hardcoded list could not detect that drift (it would pass
    while silently missing the new code).

    Matches both ``omh:`` and ``ieee:`` namespaced codes (JHE seeds at least one
    ``ieee:`` body schema, e.g. ``ieee:sleep-stage-summary:1.0``). Codes from
    other systems that are not omh-shim-served schema bodies — notably the FHIR
    ``QuestionnaireResponse`` concept — have no ``<ns>:<name>:<ver>`` shape and
    are intentionally excluded; omh-shim is not expected to serve them.
    """
    if not _SEED_PY.exists():
        pytest.skip(f"seed.py not found at {_SEED_PY} (drift check needs the monorepo checkout)")
    text = _SEED_PY.read_text()
    match = re.search(r"def seed_codeable_concepts\b.*?(?=\n    def |\Z)", text, re.DOTALL)
    block = match.group(0) if match else ""
    codes = re.findall(r"(?:omh|ieee):[a-z0-9-]+:[0-9.]+", block)
    if not codes:
        pytest.fail("Parsed no OMH/IEEE codes from seed_codeable_concepts; the parser is likely stale")
    return {short_name(c) for c in codes}


def test_all_schema_ids_from_shim():
    ids = all_schema_ids()
    assert "omh:heart-rate:2.0" in ids
    assert "omh:blood-pressure:4.0" in ids
    assert "omh:blood-glucose:4.0" in ids
    assert len(ids) >= 10


def test_covers_jhe_seeded_codes():
    """omh-shim must serve every OMH/IEEE code JHE seeds (guards version drift)."""
    seeded = _jhe_seeded_short_names()
    missing = seeded - set(all_short_names())
    assert not missing, f"omh-shim is missing JHE-seeded code(s): {sorted(missing)}"


def test_short_name_extraction():
    assert short_name("omh:heart-rate:2.0") == "heart-rate"
    assert short_name("omh:blood-pressure:4.0") == "blood-pressure"
    assert short_name("ieee:sleep-stage-summary:1.0") == "sleep-stage-summary"
    assert short_name("local:heart-rate-variability:1.0") == "heart-rate-variability"


def test_lookup_code_known():
    code = lookup_code("heart-rate")
    assert code == "https://w3id.org/openmhealth|omh:heart-rate:2.0"


def test_lookup_code_ieee_uses_ieee_system():
    """IEEE-namespaced codes resolve under the IEEE 1752 system, not the OMH one."""
    code = lookup_code("sleep-stage-summary")
    assert code == "https://w3id.org/ieee1752|ieee:sleep-stage-summary:1.0"


def test_lookup_code_unknown():
    assert lookup_code("nonexistent") is None


def test_all_short_names():
    names = all_short_names()
    assert "heart-rate" in names
    assert "blood-pressure" in names
    assert "blood-glucose" in names
    assert sorted(names) == names


def test_load_schema_wearable():
    schema = load_schema("omh:heart-rate:2.0")
    assert schema["type"] == "object"
    assert "heart_rate" in schema.get("properties", {})


def test_load_schema_clinical():
    schema = load_schema("omh:blood-pressure:4.0")
    assert schema["type"] == "object"
    assert "systolic_blood_pressure" in schema.get("properties", {})


def test_load_schema_unknown():
    with pytest.raises(KeyError):
        load_schema("omh:nonexistent:1.0")
