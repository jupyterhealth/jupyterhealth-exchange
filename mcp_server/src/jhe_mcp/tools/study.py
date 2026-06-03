from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from jhe_mcp.fhir.client import JheClient, JheClientError
from jhe_mcp.fhir.models import Demographics, StudyMeta, StudyPatient


async def get_study_count(*, base_url: str) -> int:
    """Total number of studies the caller can see."""
    async with JheClient(base_url) as client:
        data = await client.admin_get("studies", params={"page_size": 1})
        if not isinstance(data, dict) or "count" not in data:
            raise JheClientError(0, f"Expected a paginated studies response with 'count', got: {str(data)[:200]}")
        return int(data["count"])


async def list_studies(*, base_url: str) -> list[StudyMeta]:
    """Studies the caller can see (slim summaries)."""
    results: list[StudyMeta] = []
    async with JheClient(base_url) as client:
        params: dict[str, Any] = {}
        while True:
            data = await client.admin_get("studies", params=params or None)
            results.extend(StudyMeta.from_admin(item) for item in data.get("results", []))
            next_url = data.get("next")
            if not next_url:
                break
            # Forward ALL query params from the next URL so pagination works with any
            # scheme (?page=, ?cursor=, ?offset=&limit=, etc.). parse_qs returns lists;
            # take the first value of each key to produce a flat dict for admin_get.
            next_qs = parse_qs(urlparse(next_url).query)
            params = {k: v[0] for k, v in next_qs.items()}
    return results


async def get_study_metadata(*, study_id: str, base_url: str) -> StudyMeta | None:
    """Metadata for one study; None if not found or not authorized."""
    async with JheClient(base_url) as client:
        data = await client.admin_get(f"studies/{study_id}", treat_404_as_none=True)
        return StudyMeta.from_admin(data) if data is not None else None


async def list_study_patients(*, study_id: str, base_url: str) -> list[StudyPatient]:
    """Patients enrolled in a study."""
    async with JheClient(base_url) as client:
        data = await client.admin_get(f"studies/{study_id}/patients")
        return [StudyPatient.from_admin(item) for item in data]


async def get_patient_demographics(*, patient_id: str, base_url: str) -> Demographics | None:
    """Patient demographics via JHE's Admin API.

    Uses `/api/v1/patients/{id}` because the FHIR `/Patient/{id}` detail view
    rejects direct ID lookup with a 400; the Admin API endpoint works correctly.
    """
    async with JheClient(base_url) as client:
        data = await client.admin_get(f"patients/{patient_id}", treat_404_as_none=True)
        return Demographics.from_admin(data) if data is not None else None
