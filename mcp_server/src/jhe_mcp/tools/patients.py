"""Patient lookup via the JHE FHIR Patient search API (JHE PR #667)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from jhe_mcp.fhir.client import JheClient
from jhe_mcp.fhir.models import PatientSearchResult
from jhe_mcp.fhir.paging import bundle_total, clamp_paging, page_envelope

_DATE_PREFIXES = ("ge", "le", "gt", "lt")
# Strictly dashed YYYY-MM-DD (see observation_query._require_iso_date for why
# date.fromisoformat alone is too lenient).
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def build_patient_params(
    *,
    name: str | None = None,
    family: str | None = None,
    given: str | None = None,
    birthdate: str | None = None,
) -> dict[str, Any]:
    """FHIR Patient search params. String params are case-insensitive prefix matches.

    ``birthdate`` is ``YYYY-MM-DD`` with an optional ``ge``/``le``/``gt``/``lt``
    prefix (bare value = exact day). Raises ``ValueError`` when no criterion is
    given or the birthdate is malformed, so the tool fails before any request.
    """
    # Strip before the truthiness checks so whitespace-only values can't slip
    # past the at-least-one-criterion guard (the server skips blank filters,
    # which would turn "  " into an unfiltered list of every visible patient).
    # NOTE this guard prevents *accidental* unfiltered listings only — a broad
    # criterion (birthdate="le9999-12-31") legitimately matches everyone the
    # caller may see. The security boundary is JHE's server-side authorization.
    name = name.strip() if name else None
    family = family.strip() if family else None
    given = given.strip() if given else None
    birthdate = birthdate.strip() if birthdate else None
    params: dict[str, Any] = {}
    if name:
        params["name"] = name
    if family:
        params["family"] = family
    if given:
        params["given"] = given
    if birthdate:
        value = birthdate[2:] if birthdate[:2] in _DATE_PREFIXES else birthdate
        try:
            if not _ISO_DATE_RE.fullmatch(value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(
                f"birthdate must be YYYY-MM-DD with an optional ge/le/gt/lt prefix; got {birthdate!r}"
            ) from None
        params["birthdate"] = birthdate
    if not params:
        raise ValueError("Provide at least one of: name, family, given, birthdate.")
    return params


async def search_patients(
    *,
    name: str | None = None,
    family: str | None = None,
    given: str | None = None,
    birthdate: str | None = None,
    limit: int = 50,
    page: int = 1,
    base_url: str,
) -> dict[str, Any]:
    """One page of matching patients: {total, page, page_size, returned, has_more, patients}.

    ``limit`` is clamped to 1..MAX_PAGE_SIZE (the server's page-size cap).
    """
    params = build_patient_params(name=name, family=family, given=given, birthdate=birthdate)
    page_size, page = clamp_paging(limit, page)
    async with JheClient(base_url) as client:
        bundle = await client.fhir_get("Patient", params={**params, "_count": page_size, "_page": page})
    total = bundle_total(bundle)
    entries = bundle.get("entry", []) or []
    patients = [PatientSearchResult.from_fhir_entry(e).model_dump() for e in entries]
    return page_envelope(total=total, page=page, page_size=page_size, items_key="patients", items=patients)
