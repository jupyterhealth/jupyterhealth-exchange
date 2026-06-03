from __future__ import annotations

import asyncio

from jhe_mcp.fhir.client import JheClient
from jhe_mcp.fhir.observation_query import build_observation_params, count_with_optional_date
from jhe_mcp.tools.study import list_study_patients

# Bound on concurrent per-patient count requests for the by_patient path, so a
# large study doesn't open one connection per patient at once.
_MAX_CONCURRENT_PATIENT_COUNTS = 10


async def count_patient_observations(
    *,
    patient_id: str,
    data_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    base_url: str,
) -> int:
    """Exact number of observations for a patient (optionally filtered)."""
    params = build_observation_params(patient_id=patient_id, data_type=data_type)
    async with JheClient(base_url) as client:
        return await count_with_optional_date(client, params, start, end)


async def count_study_observations(
    *,
    study_id: str,
    data_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    by_patient: bool = False,
    base_url: str,
) -> int | dict[str, int]:
    """Observation count for a whole study, or per-patient when by_patient=True."""
    if not by_patient:
        params = build_observation_params(study_id=study_id, data_type=data_type)
        async with JheClient(base_url) as client:
            return await count_with_optional_date(client, params, start, end)

    patients = await list_study_patients(study_id=study_id, base_url=base_url)
    sem = asyncio.Semaphore(_MAX_CONCURRENT_PATIENT_COUNTS)
    async with JheClient(base_url) as client:

        async def _count(patient_id: str) -> int:
            async with sem:
                params = build_observation_params(patient_id=patient_id, data_type=data_type)
                return await count_with_optional_date(client, params, start, end)

        results = await asyncio.gather(*(_count(p.patient_id) for p in patients))
    return {p.patient_id: c for p, c in zip(patients, results, strict=True)}
