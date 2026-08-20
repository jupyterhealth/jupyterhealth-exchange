"""Fetch and digest a JHE instance's FHIR CapabilityStatement (GET /FHIR/R5/metadata).

The metadata endpoint is public by FHIR spec, so the fetch is deliberately
unauthenticated -- no bearer is ever attached. Results (including "endpoint
absent") are cached per base_url for a short TTL: capabilities only change
when the backend deploys, and an old JHE without the endpoint must not be
re-probed on every tool call.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300.0
_FETCH_TIMEOUT = 5.0
# FHIR RESTful interaction codes -> the shorter names JHE's config uses.
_INTERACTION_NAMES = {"search-type": "search"}

_cache: dict[str, tuple[float, ServerCapabilities | None]] = {}


class ResourceCapability(BaseModel):
    interactions: set[str] = Field(default_factory=set)
    search_params: dict[str, str] = Field(default_factory=dict)


class ServerCapabilities(BaseModel):
    fhir_version: str | None = None
    resources: dict[str, ResourceCapability] = Field(default_factory=dict)

    @classmethod
    def from_capability_statement(cls, statement: Any) -> ServerCapabilities | None:
        """Digest a CapabilityStatement dict; None when the body isn't one."""
        if not isinstance(statement, dict) or statement.get("resourceType") != "CapabilityStatement":
            return None
        resources: dict[str, ResourceCapability] = {}
        for rest in statement.get("rest") or []:
            for resource in rest.get("resource") or []:
                resource_type = resource.get("type")
                if not resource_type:
                    continue
                interactions = {
                    _INTERACTION_NAMES.get(item["code"], item["code"])
                    for item in resource.get("interaction") or []
                    if item.get("code")
                }
                params = {
                    item["name"]: item.get("type", "") for item in resource.get("searchParam") or [] if item.get("name")
                }
                resources[resource_type] = ResourceCapability(interactions=interactions, search_params=params)
        return cls(fhir_version=statement.get("fhirVersion"), resources=resources)


async def fetch_capabilities(base_url: str) -> ServerCapabilities | None:
    """One uncached fetch; None when the endpoint is absent, unreachable, or not a CapabilityStatement."""
    url = f"{base_url.rstrip('/')}/FHIR/R5/metadata"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            response = await client.get(url)  # public endpoint: no Authorization header
    except httpx.HTTPError as exc:
        logger.info("Capability fetch failed for %s: %s", url, exc)
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return ServerCapabilities.from_capability_statement(body)


async def get_capabilities(base_url: str) -> ServerCapabilities | None:
    """TTL-cached capabilities for a JHE instance (None = unknown, cached too)."""
    cached = _cache.get(base_url)
    now = time.monotonic()
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    capabilities = await fetch_capabilities(base_url)
    _cache[base_url] = (now, capabilities)
    return capabilities


def check_search_support(
    capabilities: ServerCapabilities | None, resource_type: str, param_names: Iterable[str]
) -> None:
    """Raise when this instance is KNOWN not to support one of ``param_names``.

    Fails open (no-op) when capabilities are None: an instance without the
    metadata endpoint keeps today's send-and-hope behavior.
    """
    if capabilities is None:
        return
    resource = capabilities.resources.get(resource_type)
    if resource is None:
        raise ValueError(f"This JHE instance does not serve {resource_type} (per its CapabilityStatement).")
    missing = sorted(set(param_names) - set(resource.search_params))
    if missing:
        raise ValueError(
            f"This JHE instance does not support {resource_type} search on: {', '.join(missing)} "
            "(per its CapabilityStatement)."
        )


async def preflight_observation_dates(base_url: str, start: str | None, end: str | None) -> None:
    """Before a dated observation query: error if this instance can't filter by date."""
    if not (start or end):
        return
    check_search_support(await get_capabilities(base_url), "Observation", ["date"])
