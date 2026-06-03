import pytest


@pytest.mark.asyncio
async def test_observation_tools_registered(monkeypatch):
    monkeypatch.setenv("JHE_BASE_URL", "http://localhost:8400")
    monkeypatch.setenv("JHE_CLIENT_ID", "test-client")
    monkeypatch.setenv("MCP_RESOURCE_URL", "http://localhost:8401/")
    from jhe_mcp.config import Settings
    from jhe_mcp.core import build_server

    mcp = build_server(Settings.from_env())
    # Server instructions are sent to every client on initialize.
    assert mcp.instructions and "get_patient_date_range" in mcp.instructions
    names = {tool.name for tool in await mcp.list_tools()}
    assert {
        "get_patient_observations",
        "count_patient_observations",
        "count_study_observations",
        "summarize_patient_observations",
        "get_patient_date_range",
    } <= names
