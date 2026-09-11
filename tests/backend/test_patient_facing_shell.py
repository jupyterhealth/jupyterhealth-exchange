"""The two patient-facing shell pages: config injected from the client row, components rolled up, app started."""

import json

import pytest
from oauth2_provider.models import get_application_model

from core.models import DataSource, JheClient
from core.views.patient_facing import _expected_resource_types

SHARED_COMPONENTS = ("t-hub", "t-consent", "t-done", "t-manage", "t-importing", "t-error", "t-receipt", "t-rail")


def _config(html):
    return json.loads(html.split('id="pf_config"', 1)[1].split(">", 1)[1].split("</script>", 1)[0])


def _assert_shell(html, client_component, entrypoint):
    assert 'id="pf_main"' in html and "common/js/patient-facing.js" in html
    for component in SHARED_COMPONENTS + (client_component,):
        assert f'id="{component}"' in html, f"missing component {component}"
    assert html.index('id="t-error"') < html.index(f'id="{client_component}"') < html.index(entrypoint)


def test_ow_launch_is_a_shell_configured_from_the_open_wearables_client(seeded, client):
    html = client.get("/clients/ow/launch").content.decode()
    oura = DataSource.objects.get(name="Oura")

    _assert_shell(html, "t-launch", "patientApp()")
    assert "clients/ow/js/client-ow.js" in html
    assert _config(html) == {
        "client": "ow",
        "clientId": "",
        "scope": "",
        "dataSourceIds": [oura.id],
        "sourceLabels": {str(oura.id): "Oura"},
        "expectedResourceTypes": [],
        "siteTitle": "JupyterHealth Exchange",
        "pageUrl": "/clients/ow/launch",
    }


def test_ehr_connect_is_a_shell_configured_from_the_ehr_client(seeded, client):
    html = client.get("/clients/ehr-patient-portal/").content.decode()
    ehr = DataSource.objects.get(name="EHR Patient Portal")
    aux = JheClient.objects.get(application__name="EHR Patient Portal").aux_data

    _assert_shell(html, "t-connect", "patientApp()")
    assert "fhir-client.min.js" in html and "client-ehr-patient-portal.js" in html
    config = _config(html)
    assert config["client"] == "ehr-patient-portal" and config["pageUrl"] == "/clients/ehr-patient-portal/"
    assert config["clientId"] == aux["client_id"] and config["scope"] == aux["scopes"]
    assert config["dataSourceIds"] == [ehr.id] and config["sourceLabels"] == {str(ehr.id): "EHR Patient Portal"}
    assert config["expectedResourceTypes"][:3] == ["AllergyIntolerance", "CarePlan", "CareTeam"]
    assert "Patient" in config["expectedResourceTypes"] and "openid" not in config["expectedResourceTypes"]


def test_expected_resource_types_lists_only_patient_read_scopes():
    scopes = "openid launch/patient patient/Observation.rs patient/*.read patient/Condition.read patient/Patient.read"
    assert _expected_resource_types(scopes) == ["Condition", "Patient"]


def test_ehr_callback_is_a_shell_that_runs_the_import(seeded, client):
    html = client.get("/clients/ehr-patient-portal/callback").content.decode()
    _assert_shell(html, "t-connect", "eppCallback()")
    assert _config(html)["pageUrl"] == "/clients/ehr-patient-portal/"
    assert "patientApp()" not in html


def test_config_has_no_data_sources_when_the_client_link_is_missing(db, client):
    app = get_application_model().objects.create(name="EHR Patient Portal", client_id="local-app-id")
    JheClient.objects.create(application=app, aux_data={"client_id": "epic-id", "scopes": "openid"})
    DataSource.objects.create(name="EHR Patient Portal", type="patient_app")

    config = _config(client.get("/clients/ehr-patient-portal/").content.decode())

    assert config["dataSourceIds"] == [] and config["sourceLabels"] == {}


@pytest.mark.parametrize("path", ["/clients/ow/launch", "/clients/ehr-patient-portal/"])
def test_shells_render_without_a_seeded_client(db, client, path):
    assert client.get(path).status_code == 200
