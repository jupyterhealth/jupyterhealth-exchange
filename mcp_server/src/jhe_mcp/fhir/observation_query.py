from __future__ import annotations

import logging
from datetime import date
from typing import Any

from jhe_mcp.fhir.client import JheClient, JheClientError
from jhe_mcp.fhir.models import Observation
from jhe_mcp.omh_registry import all_short_names, lookup_code

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 1000
# Upper bound on pages walked by iter_all_observations, so a backend that
# reports an enormous (or wrong) `total` can't make us page indefinitely / OOM.
# MAX_PAGE_SIZE * MAX_PAGES is the most records a single call will pull.
MAX_PAGES = 50


def _bundle_total(bundle: Any) -> int:
    """Return a FHIR search Bundle's ``total``, rejecting non-Bundle responses.

    JHE returns 200 with a search Bundle for Observation queries. If the body is
    something else (an error envelope, a non-dict), reading ``total`` would
    otherwise default to 0 and silently report "no observations" for what is
    actually a failure — so raise instead.
    """
    if not isinstance(bundle, dict) or "total" not in bundle:
        raise JheClientError(0, f"Expected a FHIR search Bundle with 'total', got: {str(bundle)[:200]}")
    return int(bundle["total"])


def build_observation_params(
    *,
    patient_id: str | None = None,
    study_id: str | None = None,
    data_type: str | None = None,
) -> dict[str, Any]:
    """Build FHIR Observation query params shared by all observation tools.

    Date filtering is intentionally NOT included here: the JHE FHIR Observation
    endpoint does not parse a ``date`` parameter, so any date window is applied
    client-side (see ``in_date_range`` / ``collect_observations``).
    """
    params: dict[str, Any] = {}
    if study_id is not None:
        params["patient._has:_group:member:_id"] = study_id
    if patient_id is not None:
        params["patient"] = patient_id
    if data_type:
        code = lookup_code(data_type)
        if code is None:
            raise ValueError(f"Unknown data_type {data_type!r}. Known: {all_short_names()}")
        params["code"] = code
    return params


def _require_iso_date(value: str | None, label: str) -> None:
    """Validate a date-window bound is ISO ``YYYY-MM-DD``, or raise a clear error.

    Called once at the filtering choke point so a malformed tool argument fails
    with an actionable message rather than a raw ValueError surfacing mid-filter.
    """
    if value is None:
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} must be an ISO date (YYYY-MM-DD); got {value!r}") from None


def in_date_range(effective_at: str | None, start: str | None, end: str | None) -> bool:
    """Inclusive date-window check on an observation's effective timestamp.

    Parses the date portion of an ISO-8601 ``effective_at`` and compares it to
    ``start``/``end`` (``YYYY-MM-DD``). Observations whose effective timestamp is
    absent (``None``) or not parseable as an ISO date are treated as out of range
    when a window is given: they cannot be confidently placed in time, so the
    previous ``effective_at[:10]`` string slice — which would mis-filter a
    non-ISO timestamp silently — is replaced with an explicit parse + skip.
    """
    if effective_at is None:
        return False
    try:
        day = date.fromisoformat(effective_at[:10])
    except ValueError:
        logger.warning("Skipping observation with non-ISO effective_at during date filtering")
        return False
    if start and day < date.fromisoformat(start):
        return False
    if end and day > date.fromisoformat(end):
        return False
    return True


async def count_observations(client: JheClient, params: dict[str, Any]) -> int:
    """Exact count via the bundle `total`, requesting a single record."""
    bundle = await client.fhir_get("Observation", params={**params, "_count": 1})
    return _bundle_total(bundle)


async def fetch_observation_page(
    client: JheClient,
    params: dict[str, Any],
    *,
    page: int,
    page_size: int,
) -> tuple[int, list[dict], bool]:
    """Return (total, entries, has_more) for one FHIR page."""
    bundle = await client.fhir_get("Observation", params={**params, "_count": page_size, "_page": page})
    total = _bundle_total(bundle)
    entries = bundle.get("entry", []) or []
    has_more = page * page_size < total
    return total, entries, has_more


async def iter_all_observations(client: JheClient, params: dict[str, Any]) -> list[dict]:
    """Page through every matching entry server-side (raw bundle entries).

    Bounded by ``MAX_PAGES``; if the result set is larger, we stop and log a
    warning rather than paging indefinitely (the date-filtered / summarize paths
    fetch everything because JHE ignores the ``date`` param).
    """
    out: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        total, entries, has_more = await fetch_observation_page(client, params, page=page, page_size=MAX_PAGE_SIZE)
        out.extend(entries)
        if not has_more or not entries:
            return out
    logger.warning(
        "iter_all_observations hit MAX_PAGES=%d (%d records) for params=%s; result truncated",
        MAX_PAGES,
        len(out),
        params,
    )
    return out


async def collect_observations(
    client: JheClient,
    params: dict[str, Any],
    *,
    start: str | None = None,
    end: str | None = None,
) -> list[Observation]:
    """Fetch all matching observations, applying a client-side date window.

    The backend ignores date params, so when ``start``/``end`` are supplied we
    fetch the full (patient/study/code-scoped) set and filter in process on each
    record's ``effective_at``.
    """
    _require_iso_date(start, "start")
    _require_iso_date(end, "end")
    entries = await iter_all_observations(client, params)
    observations = [Observation.from_fhir_entry(e) for e in entries]
    if start or end:
        observations = [o for o in observations if in_date_range(o.effective_at, start, end)]
    return observations


async def count_with_optional_date(
    client: JheClient,
    params: dict[str, Any],
    start: str | None,
    end: str | None,
) -> int:
    """Count observations, using the cheap bundle `total` when no date window is
    given, and a client-side filtered full fetch when one is."""
    if not (start or end):
        return await count_observations(client, params)
    return len(await collect_observations(client, params, start=start, end=end))
