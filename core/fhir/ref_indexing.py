"""Index-refs pass for auxiliary FHIR resources (issue #584).

When a FHIR aux resource is uploaded its references still point at the upstream system's ids
(e.g. ``Encounter.subject.reference = "Patient/123"``), while JHE stores each resource under a
new UUID. This pass rewrites the references declared in ``__refPaths`` (fhir_config.json) from
``"<Type>/<upstreamId>"`` to ``"<Type>/<JHE-UUID>"`` by looking up the target row's
``fhir_resource_id`` within the SAME FhirSource. Unresolvable references become
``"not-found:<upstreamId>"``.

Scope (kept deliberately small, see #584): same-FhirSource lookup and scalar dotted ref paths
only. Expanding the lookup across a patient's sources, and supporting array-valued ref paths,
are tracked as follow-ups.
"""

from core.fhir.config import ref_paths_for


def _leaf(body, path):
    """Resolve a dotted ``path`` to its leaf, returning ``(container, key, value)`` or ``None``.

    Only walks plain dict hops (scalar paths); a missing or non-dict hop yields ``None``.
    """
    node = body
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(node, dict) or not isinstance(node.get(part), dict):
            return None
        node = node[part]
    key = parts[-1]
    if not isinstance(node, dict) or key not in node:
        return None
    return node, key, node[key]


def index_fhir_source_refs(fhir_source):
    """Rewrite references in every not-yet-indexed aux row of ``fhir_source``.

    Returns a summary dict: ``rows_indexed``, ``refs_rewritten``, ``refs_not_found``.
    """
    rows = list(fhir_source.aux_resources.all())
    # (resource_type, upstream_id) -> JHE uuid, for resolving references within this source.
    target_index = {(row.resource_type, str(row.fhir_resource_id)): str(row.pk) for row in rows if row.fhir_resource_id}
    # Ids already equal to a row's JHE pk are previously-resolved refs; leave them untouched so
    # re-indexing is idempotent and never flips a resolved ref to not-found.
    source_pks = {str(row.pk) for row in rows}

    summary = {"rows_indexed": 0, "refs_rewritten": 0, "refs_not_found": 0}
    for row in [r for r in rows if not r.ref_indexed]:
        body = row.fhir_data or {}
        for path in ref_paths_for(row.resource_type):
            found = _leaf(body, path)
            if not found:
                continue
            container, key, value = found
            if not isinstance(value, str) or "/" not in value:
                continue  # not a "Type/id" literal reference
            rtype, _, upstream_id = value.partition("/")
            if upstream_id in source_pks:
                continue  # already a JHE id (previously resolved); leave it
            target_uuid = target_index.get((rtype, upstream_id))
            if target_uuid:
                container[key] = f"{rtype}/{target_uuid}"
                summary["refs_rewritten"] += 1
            else:
                container[key] = f"not-found:{upstream_id}"
                summary["refs_not_found"] += 1
        row.fhir_data = body
        row.ref_indexed = True
        row.save(update_fields=["fhir_data", "ref_indexed"])
        summary["rows_indexed"] += 1
    return summary
