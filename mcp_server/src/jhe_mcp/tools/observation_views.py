from __future__ import annotations

from typing import Any

from jhe_mcp.fhir.client import JheClient
from jhe_mcp.fhir.models import Observation, SlimObservation
from jhe_mcp.fhir.observation_query import (
    build_observation_params,
    collect_observations,
    fetch_observation_page,
)
from jhe_mcp.fhir.paging import clamp_paging, page_envelope

_ORDER_TO_SORT = {"newest": "-date", "oldest": "date"}

# Entries fetched per date-range boundary, letting the pick skip records whose
# effective time is unparseable client-side.
_BOUNDARY_PROBE = 5
# The descending boundary probe adds this filter so records with NO effective
# time (which the server's DESC sort places first — SQL NULLS FIRST) are
# excluded server-side; without it, a cluster of undated records would make
# `latest` come back None despite dated data existing.
_ANY_DATE_FILTER = ["ge0001-01-01"]


def _first_effective_at(entries: list[dict]) -> str | None:
    for entry in entries:
        at = Observation.from_fhir_entry(entry).effective_at
        if at:
            return at
    return None


async def summarize_patient_observations(
    *,
    patient_id: str,
    start: str | None = None,
    end: str | None = None,
    base_url: str,
) -> dict[str, Any]:
    """Per-data-type digest for a patient: {truncated, types: {type: {count, earliest, latest}}}.

    Aggregation is client-side (the server has no group-by, and the summary must
    include data types outside the OMH registry), but any date window bounds the
    fetch server-side. ``truncated`` is True when the bounded fetch could not
    cover every record, meaning the per-type counts are lower bounds.
    """
    params = build_observation_params(patient_id=patient_id, start=start, end=end)
    async with JheClient(base_url) as client:
        observations, truncated = await collect_observations(client, params)
    types: dict[str, dict[str, Any]] = {}
    for obs in observations:
        key = obs.code_display or obs.code or "unknown"
        bucket = types.setdefault(key, {"count": 0, "earliest": None, "latest": None})
        bucket["count"] += 1
        at = obs.effective_at
        if at:
            if bucket["earliest"] is None or at < bucket["earliest"]:
                bucket["earliest"] = at
            if bucket["latest"] is None or at > bucket["latest"]:
                bucket["latest"] = at
    return {"truncated": truncated, "types": types}


async def get_patient_date_range(
    *,
    patient_id: str,
    base_url: str,
) -> dict[str, Any]:
    """Earliest/latest observation timestamp and total count for a patient.

    Two cheap server-side queries: a few records sorted ascending by effective
    date (whose bundle also carries the total), then the newest dated record
    (the descending probe filters out undated records server-side). ``count``
    includes undated records; ``earliest``/``latest`` are ISO-8601 strings from
    dated records only (``None`` when there are none).
    """
    params = build_observation_params(patient_id=patient_id)
    async with JheClient(base_url) as client:
        total, first_entries, _ = await fetch_observation_page(
            client, params, page=1, page_size=_BOUNDARY_PROBE, sort="date"
        )
        if total == 0:
            return {"earliest": None, "latest": None, "count": 0}
        _, last_entries, _ = await fetch_observation_page(
            client,
            {**params, "date": _ANY_DATE_FILTER},
            page=1,
            page_size=_BOUNDARY_PROBE,
            sort="-date",
        )
    return {
        "earliest": _first_effective_at(first_entries),
        "latest": _first_effective_at(last_entries),
        "count": total,
    }


async def get_patient_observations(
    *,
    patient_id: str,
    data_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    verbosity: str = "slim",
    order: str = "newest",
    limit: int = 50,
    page: int = 1,
    base_url: str,
) -> dict[str, Any]:
    """One page of a patient's observations with total/has_more awareness.

    verbosity="slim" (default) omits the raw OMH body; "full" includes it.
    Date windows and ordering are applied server-side (FHIR ``date`` + ``_sort``).
    ``limit`` is clamped to 1..MAX_PAGE_SIZE (the server's page cap).
    """
    if verbosity not in ("slim", "full"):
        raise ValueError(f"verbosity must be 'slim' or 'full', got {verbosity!r}")
    sort = _ORDER_TO_SORT.get(order)
    if sort is None:
        raise ValueError(f"order must be 'newest' or 'oldest', got {order!r}")
    params = build_observation_params(patient_id=patient_id, data_type=data_type, start=start, end=end)
    page_size, page = clamp_paging(limit, page)
    async with JheClient(base_url) as client:
        total, entries, _ = await fetch_observation_page(client, params, page=page, page_size=page_size, sort=sort)
    observations = [Observation.from_fhir_entry(e) for e in entries]
    if verbosity == "slim":
        payload = [SlimObservation.from_observation(o).model_dump() for o in observations]
    else:
        payload = [o.model_dump() for o in observations]
    return page_envelope(total=total, page=page, page_size=page_size, items_key="observations", items=payload)
