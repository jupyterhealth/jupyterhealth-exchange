import pytest

from core import utils


@pytest.fixture(scope="session")
def registry():
    return utils.build_schema_registry()


def test_build_schema_registry():
    registry = utils.build_schema_registry()
    url = "https://w3id.org/ieee/ieee-1752-schema/data-point-1.0.json"
    registry.resolver().lookup(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://w3id.org/ieee/ieee-1752-schema/data-point-1.0.json",
        "https://w3id.org/ieee/ieee-1752-schema/food-entry-0.1.json",
        "https://w3id.org/openmhealth/schemas/omh/heart-rate-2.0.json",
        "https://w3id.org/openmhealth/schemas/omh/body-weight-3.0.json",
    ],
)
def test_registry(registry, url):
    resolver = registry.resolver()
    resolver.lookup(url)


@pytest.mark.parametrize(
    "code, schema_id",
    [
        ("omh:heart-rate:2.0", "https://w3id.org/openmhealth/schemas/omh/heart-rate-2.0.json"),
        ("ieee:food-entry:0.1", "https://w3id.org/ieee/ieee-1752-schema/food-entry-0.1.json"),
        ("ieee:physical-activity:1.0", "https://w3id.org/ieee/ieee-1752-schema/physical-activity-1.0.json"),
    ],
)
def test_code_to_schema(registry, code, schema_id):
    schema = utils.code_to_schema(code)
    assert schema == {"$ref": schema_id}
    assert registry.resolver().lookup(schema_id)
