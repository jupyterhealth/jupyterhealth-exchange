from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Awaitable, Callable

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from jhe_mcp.auth.oauth_flow import AuthenticationRequired
from jhe_mcp.auth.token_verifier import JheTokenVerifier
from jhe_mcp.config import Settings
from jhe_mcp.omh_registry import all_schema_ids, all_short_names, load_schema, short_name
from jhe_mcp.tools import observation_counts, observation_views
from jhe_mcp.tools import study as study_tools

logger = logging.getLogger(__name__)

AUTH_REQUIRED_MSG = (
    "Authentication required. Open this URL in your browser to log in:\n\n"
    "{url}\n\n"
    "After logging in, retry your request."
)

# Sent to every client in the MCP `initialize` handshake; compliant clients
# surface this to the model so any connecting LLM knows how to use the server.
SERVER_INSTRUCTIONS = """\
JupyterHealth Exchange (JHE) MCP server — access to patient-consented health data.

Model: studies contain enrolled patients; patients have observations (measurements)
encoded as Open mHealth (OMH) bodies. Every tool runs as the authenticated user and
only returns data that user is authorized to see.

Tools by purpose:
- Studies: get_study_count, list_studies, get_study_metadata, list_study_patients
- Patients: get_patient_demographics, get_patient_date_range
- Observations: count_patient_observations, count_study_observations,
  summarize_patient_observations, get_patient_observations
- OMH schemas: get_omh_schema(name); also browsable as resources at omh://schema/<name>

Key behaviors:
- Dates are OPTIONAL on observation tools. Pass start/end as YYYY-MM-DD (inclusive)
  to filter by the observation's effective time; omit them to include everything.
- get_patient_observations returns {total, page, page_size, returned, has_more,
  observations}. It defaults to compact "slim" records (type, time, value, unit) and
  omits the raw OMH body. Pass verbosity="full" only when you need raw bodies. Page
  with limit/page and use total/has_more to know when you've read everything.

Efficient workflows — do NOT dump or page through records just to count or find dates:
- Counts: use count_patient_observations / count_study_observations
  (count_study_observations(by_patient=true) returns {patient_id: count} in one call).
- First/last data for a patient: get_patient_date_range(patient_id) ->
  {earliest, latest, count}. Never page to the end to find min/max dates.
- "Show me everything" for a patient: start with summarize_patient_observations
  (per-type {count, earliest, latest}) for the overview, then page
  get_patient_observations (slim); fetch verbosity="full" only for specific records.
- Need a data type's schema: get_omh_schema("blood-glucose"), etc.
"""


def build_server(
    settings: Settings,
    pre_tool_hook: Callable[[], Awaitable[None]] | None = None,
) -> FastMCP:
    parsed = urllib.parse.urlparse(settings.mcp_resource_url)
    public_host = parsed.netloc  # host[:port]
    public_origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed_hosts = [public_host, f"{public_host}:*", "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    allowed_origins = [public_origin, "http://localhost:*", "http://127.0.0.1:*"]
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    mcp = FastMCP(
        name="jhe-mcp",
        instructions=SERVER_INSTRUCTIONS,
        transport_security=transport_security,
        token_verifier=JheTokenVerifier(settings),
        auth=AuthSettings(
            # Our broker is the authorization server the clients use.
            issuer_url=settings.mcp_resource_url,
            resource_server_url=settings.mcp_resource_url,
            required_scopes=None,
        ),
    )
    base_url = settings.jhe_base_url

    async def _before() -> str | None:
        if pre_tool_hook is not None:
            try:
                await pre_tool_hook()
            except AuthenticationRequired as exc:
                return AUTH_REQUIRED_MSG.format(url=exc.url)
        return None

    def _make_schema_reader(sid: str):
        async def _read() -> str:
            return json.dumps(load_schema(sid), indent=2)

        return _read

    for schema_id in sorted(all_schema_ids()):
        sname = short_name(schema_id)
        uri = f"omh://schema/{sname}"
        try:
            schema = load_schema(schema_id)
            description = schema.get("description", f"OMH schema: {schema_id}")
            reader = _make_schema_reader(schema_id)
            reader.__doc__ = description
            reader.__name__ = f"omh_schema_{sname.replace('-', '_')}"
            mcp.resource(uri)(reader)
        except (KeyError, FileNotFoundError) as exc:
            logger.warning("Skipping OMH schema resource %s: %s", schema_id, exc)

    @mcp.tool()
    async def get_omh_schema(name: str) -> dict | str:
        """Return the full OMH JSON schema for a data type short name.

        Known names: blood-glucose, blood-pressure, body-temperature,
        heart-rate, heart-rate-variability, oxygen-saturation,
        physical-activity, respiratory-rate, rr-interval,
        sleep-duration, sleep-episode, step-count.
        """
        if auth_msg := await _before():
            return auth_msg
        for sid in all_schema_ids():
            if short_name(sid) == name:
                return load_schema(sid)
        return {"error": f"Unknown schema name {name!r}", "known": all_short_names()}

    @mcp.tool()
    async def get_study_count() -> int | str:
        """How many studies the authenticated user can see."""
        if auth_msg := await _before():
            return auth_msg
        return await study_tools.get_study_count(base_url=base_url)

    @mcp.tool()
    async def list_studies() -> list[dict] | str:
        """List studies the authenticated user can see."""
        if auth_msg := await _before():
            return auth_msg
        studies = await study_tools.list_studies(base_url=base_url)
        return [s.model_dump() for s in studies]

    @mcp.tool()
    async def get_study_metadata(study_id: str) -> dict | str | None:
        """Metadata for one study by ID. Returns null if not found or not authorized."""
        if auth_msg := await _before():
            return auth_msg
        meta = await study_tools.get_study_metadata(study_id=study_id, base_url=base_url)
        return meta.model_dump() if meta else None

    @mcp.tool()
    async def list_study_patients(study_id: str) -> list[dict] | str:
        """Patients enrolled in a study — returns ID, name, and email for each."""
        if auth_msg := await _before():
            return auth_msg
        patients = await study_tools.list_study_patients(study_id=study_id, base_url=base_url)
        return [p.model_dump() for p in patients]

    @mcp.tool()
    async def get_patient_demographics(patient_id: str) -> dict | str | None:
        """Slim demographics for a patient by ID."""
        if auth_msg := await _before():
            return auth_msg
        d = await study_tools.get_patient_demographics(patient_id=patient_id, base_url=base_url)
        return d.model_dump() if d else None

    @mcp.tool()
    async def get_patient_observations(
        patient_id: str,
        data_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
        verbosity: str = "slim",
        limit: int = 50,
        page: int = 1,
    ) -> dict | str:
        """One page of a patient's observations, with total and pagination.

        Returns {total, page, page_size, returned, has_more, observations}.
        verbosity='slim' (default) returns compact records (type, time,
        value/unit) and omits the raw OMH body; verbosity='full' includes it.
        Filter by OMH data type short name (e.g. 'blood-glucose') and ISO dates.
        """
        if auth_msg := await _before():
            return auth_msg
        return await observation_views.get_patient_observations(
            patient_id=patient_id,
            data_type=data_type,
            start=start,
            end=end,
            verbosity=verbosity,
            limit=limit,
            page=page,
            base_url=base_url,
        )

    @mcp.tool()
    async def count_patient_observations(
        patient_id: str,
        data_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> int | str:
        """Exact number of observations for a patient, without returning records."""
        if auth_msg := await _before():
            return auth_msg
        return await observation_counts.count_patient_observations(
            patient_id=patient_id, data_type=data_type, start=start, end=end, base_url=base_url
        )

    @mcp.tool()
    async def count_study_observations(
        study_id: str,
        data_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
        by_patient: bool = False,
    ) -> int | dict | str:
        """Observation count across a whole study in one call.

        With by_patient=True, returns {patient_id: count} instead of a total.
        """
        if auth_msg := await _before():
            return auth_msg
        return await observation_counts.count_study_observations(
            study_id=study_id,
            data_type=data_type,
            start=start,
            end=end,
            by_patient=by_patient,
            base_url=base_url,
        )

    @mcp.tool()
    async def summarize_patient_observations(
        patient_id: str,
        start: str | None = None,
        end: str | None = None,
    ) -> dict | str:
        """Compact per-data-type digest for a patient: {type: {count, earliest, latest}}.

        Use this for 'show me everything' overviews instead of dumping records.
        """
        if auth_msg := await _before():
            return auth_msg
        return await observation_views.summarize_patient_observations(
            patient_id=patient_id, start=start, end=end, base_url=base_url
        )

    @mcp.tool()
    async def get_patient_date_range(patient_id: str) -> dict | str:
        """Earliest and latest observation dates and total count for a patient.

        Returns {earliest, latest, count}. Use this for first/last-data questions
        instead of paging through all of a patient's observations.
        """
        if auth_msg := await _before():
            return auth_msg
        return await observation_views.get_patient_date_range(patient_id=patient_id, base_url=base_url)

    return mcp
