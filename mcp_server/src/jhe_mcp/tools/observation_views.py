from __future__ import annotations

from typing import Any

from jhe_mcp.fhir.client import JheClient
from jhe_mcp.fhir.models import Observation, SlimObservation
from jhe_mcp.fhir.observation_query import (
    build_observation_params,
    collect_observations,
    fetch_observation_page,
)


async def summarize_patient_observations(
    *,
    patient_id: str,
    start: str | None = None,
    end: str | None = None,
    base_url: str,
) -> dict[str, dict[str, Any]]:
    """Per-data-type digest for a patient: {type: {count, earliest, latest}}."""
    params = build_observation_params(patient_id=patient_id)
    async with JheClient(base_url) as client:
        observations = await collect_observations(client, params, start=start, end=end)
    summary: dict[str, dict[str, Any]] = {}
    for obs in observations:
        key = obs.code_display or obs.code or "unknown"
        bucket = summary.setdefault(key, {"count": 0, "earliest": None, "latest": None})
        bucket["count"] += 1
        at = obs.effective_at
        if at:
            if bucket["earliest"] is None or at < bucket["earliest"]:
                bucket["earliest"] = at
            if bucket["latest"] is None or at > bucket["latest"]:
                bucket["latest"] = at
    return summary


async def get_patient_date_range(
    *,
    patient_id: str,
    base_url: str,
) -> dict[str, Any]:
    """Earliest/latest observation timestamp and total count for a patient.

    Fetches the patient's observations server-side and reduces to a compact
    ``{earliest, latest, count}`` so the caller gets exact first/last dates in a
    single call instead of paging to the end. ``earliest``/``latest`` are
    ISO-8601 strings (``None`` if no record has a parseable timestamp).
    """
    params = build_observation_params(patient_id=patient_id)
    async with JheClient(base_url) as client:
        observations = await collect_observations(client, params)
    dated = [o.effective_at for o in observations if o.effective_at]
    return {
        "earliest": min(dated) if dated else None,
        "latest": max(dated) if dated else None,
        "count": len(observations),
    }


async def get_patient_observations(
    *,
    patient_id: str,
    data_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    verbosity: str = "slim",
    limit: int = 50,
    page: int = 1,
    base_url: str,
) -> dict[str, Any]:
    """One page of a patient's observations with total/has_more awareness.

    verbosity="slim" (default) omits the raw OMH body; "full" includes it.
    With a start/end window the backend cannot filter by date, so the full set
    is fetched and filtered client-side, then paginated in process.
    """
    if verbosity not in ("slim", "full"):
        raise ValueError(f"verbosity must be 'slim' or 'full', got {verbosity!r}")
    params = build_observation_params(patient_id=patient_id, data_type=data_type)
    page_size = max(1, min(int(limit), 1000))
    page = max(1, int(page))

    if start or end:
        async with JheClient(base_url) as client:
            filtered = await collect_observations(client, params, start=start, end=end)
        total = len(filtered)
        offset = (page - 1) * page_size
        observations = filtered[offset : offset + page_size]
        has_more = offset + page_size < total
    else:
        async with JheClient(base_url) as client:
            total, entries, has_more = await fetch_observation_page(client, params, page=page, page_size=page_size)
        observations = [Observation.from_fhir_entry(e) for e in entries]

    if verbosity == "slim":
        payload = [SlimObservation.from_observation(o).model_dump() for o in observations]
    else:
        payload = [o.model_dump() for o in observations]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "returned": len(payload),
        "has_more": has_more,
        "observations": payload,
    }
