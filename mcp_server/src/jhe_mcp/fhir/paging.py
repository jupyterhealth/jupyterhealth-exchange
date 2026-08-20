"""Shared FHIR search paging: the server's page cap, bundle validation, page envelopes."""

from __future__ import annotations

from typing import Any

from jhe_mcp.fhir.client import JheClientError

# Mirrors the server's FHIRBundlePagination.max_page_size (core/fhir/pagination.py).
# If that cap ever changes, this clamp and the has_more arithmetic drift with it —
# keep the two in sync.
MAX_PAGE_SIZE = 1000


def bundle_total(bundle: Any) -> int:
    """Return a FHIR search Bundle's ``total``, rejecting non-Bundle responses.

    JHE returns 200 with a search Bundle for FHIR queries. If the body is
    something else (an error envelope, a non-dict), reading ``total`` would
    otherwise default to 0 and silently report "no results" for what is
    actually a failure — so raise instead.
    """
    if not isinstance(bundle, dict) or "total" not in bundle:
        raise JheClientError(0, f"Expected a FHIR search Bundle with 'total', got: {str(bundle)[:200]}")
    return int(bundle["total"])


def clamp_paging(limit: int, page: int) -> tuple[int, int]:
    """Clamp tool paging args to (page_size 1..MAX_PAGE_SIZE, page >= 1)."""
    return max(1, min(int(limit), MAX_PAGE_SIZE)), max(1, int(page))


def page_envelope(*, total: int, page: int, page_size: int, items_key: str, items: list) -> dict[str, Any]:
    """The paged response shape shared by every list-returning tool."""
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "returned": len(items),
        "has_more": page * page_size < total,
        items_key: items,
    }
