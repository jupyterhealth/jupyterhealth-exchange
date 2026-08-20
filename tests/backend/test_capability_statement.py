"""GET /FHIR/R5/metadata and /.well-known/smart-configuration — public discovery (issue #615)."""

import pytest
from rest_framework.test import APIClient

from core.fhir.fhir_validation import validate_fhir_resource

pytestmark = pytest.mark.django_db


def _fetch(**extra):
    return APIClient().get("/FHIR/R5/metadata", **extra)  # deliberately unauthenticated


def _resource(statement, resource_type):
    return next(r for r in statement["rest"][0]["resource"] if r["type"] == resource_type)


def _param(entry, name):
    return next(p for p in entry["searchParam"] if p["name"] == name)


def test_metadata_is_public_and_valid():
    response = _fetch()
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "CapabilityStatement"
    assert body["kind"] == "instance"
    assert body["fhirVersion"] == "5.0.0"
    assert body["format"] == ["json"]
    assert body["software"] == {"name": "JupyterHealth Exchange"}
    validate_fhir_resource("CapabilityStatement", body)  # fhir.resources parse = R5-valid


def test_metadata_public_even_with_invalid_bearer():
    # "Capabilities SHALL be retrievable" must hold when a client attaches a
    # bad/expired token — DRF authentication must not intercept with a 401.
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer totally-invalid-token")
    assert client.get("/FHIR/R5/metadata").status_code == 200


def test_metadata_accepts_fhir_json_media_type():
    response = _fetch(HTTP_ACCEPT="application/fhir+json")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/fhir+json")
    # Plain JSON continues to work.
    assert _fetch(HTTP_ACCEPT="application/json").status_code == 200


def test_metadata_head_cache_and_cors():
    response = _fetch()
    assert "max-age=3600" in response["Cache-Control"]
    assert response["Access-Control-Allow-Origin"] == "*"  # browser SMART apps read this cross-origin
    assert APIClient().head("/FHIR/R5/metadata").status_code == 200


def test_metadata_date_is_stable_across_requests():
    # The statement describes deploy-time capabilities; a per-request timestamp
    # would defeat caching and misuse the R5 "date last changed" semantics.
    assert _fetch().json()["date"] == _fetch().json()["date"]


def test_metadata_builds_without_database_queries(django_assert_num_queries):
    from django.test import RequestFactory

    from core.fhir.capability import build_capability_statement

    request = RequestFactory().get("/FHIR/R5/metadata")
    with django_assert_num_queries(0):
        build_capability_statement(request)


def test_observation_capabilities_reflect_config():
    observation = _resource(_fetch().json(), "Observation")
    codes = {i["code"] for i in observation["interaction"]}
    assert {"create", "read", "search-type"} <= codes
    params = {p["name"]: p["type"] for p in observation["searchParam"]}
    assert params["date"] == "date"
    assert params["code"] == "token"
    # The OMH payload encoding is stated as documentation (no StructureDefinition yet).
    assert "w3id.org/openmhealth" in observation["documentation"]
    assert "valueAttachment" in observation["documentation"]


def test_mapped_types_do_not_claim_model_side_update_delete_unqualified():
    # core/views/fhir.py 405s mapped update/delete, so where those interactions
    # are declared (via the aux store) the entry must say they are import-only.
    observation = _resource(_fetch().json(), "Observation")
    codes = {i["code"] for i in observation["interaction"]}
    if {"update", "delete"} & codes:
        assert "imported records only" in observation["documentation"]


def test_patient_capabilities_union_mapped_and_aux():
    patient = _resource(_fetch().json(), "Patient")
    assert {"read", "search-type"} <= {i["code"] for i in patient["interaction"]}
    params = {p["name"]: p["type"] for p in patient["searchParam"]}
    assert params["birthdate"] == "date"
    assert params["family"] == "string" and params["given"] == "string" and params["name"] == "string"
    assert "identifier" in params  # canonical param — filters on the default search
    # Aux-only params are declared but must carry the _source caveat: the
    # default (JHE-native) search silently ignores them.
    death_date = _param(patient, "death-date")
    assert "_source" in death_date["documentation"]
    assert "documentation" not in _param(patient, "identifier")  # canonical: no caveat


def test_common_search_params_declared():
    patient = _resource(_fetch().json(), "Patient")
    params = {p["name"]: p["type"] for p in patient["searchParam"]}
    assert params["_id"] == "token"
    assert params["_lastUpdated"] == "date"
    assert params["_source"] == "uri"


def test_aux_store_patch_declared():
    condition = _resource(_fetch().json(), "Condition")  # pure-aux type with "*" interactions
    codes = {i["code"] for i in condition["interaction"]}
    assert "patch" in codes
    assert _fetch().json()["patchFormat"] == ["application/json"]


def test_security_block_expectations_and_batch():
    body = _fetch().json()
    rest = body["rest"][0]
    assert rest["security"]["service"][0]["coding"][0]["code"] == "SMART-on-FHIR"
    uris = {e["url"]: e["valueUri"] for e in rest["security"]["extension"][0]["extension"]}
    assert uris["authorize"].startswith("http://testserver") and uris["authorize"].endswith("/o/authorize/")
    assert uris["token"].endswith("/o/token/")
    expectation = _resource(body, "Patient")["interaction"][0]["extension"][0]
    assert expectation["url"].endswith("capabilitystatement-expectation")
    assert expectation["valueCode"] == "SHALL"
    batch = next(i for i in rest["interaction"] if i["code"] == "batch")
    assert batch["extension"][0]["valueCode"] == "SHALL"


def test_forwarded_proto_renders_https_uris():
    body = _fetch(HTTP_X_FORWARDED_PROTO="https").json()
    assert body["implementation"]["url"].startswith("https://")
    uris = {e["url"]: e["valueUri"] for e in body["rest"][0]["security"]["extension"][0]["extension"]}
    assert uris["authorize"].startswith("https://")


def test_smart_configuration_discovery():
    response = APIClient().get("/FHIR/R5/.well-known/smart-configuration")
    assert response.status_code == 200
    body = response.json()
    assert body["authorization_endpoint"].endswith("/o/authorize/")
    assert body["token_endpoint"].endswith("/o/token/")
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "launch-standalone" in body["capabilities"]
    assert "client-public" in body["capabilities"]
    assert response["Access-Control-Allow-Origin"] == "*"
