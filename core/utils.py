# core/utils.py
import json
import logging
import random
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.utils import timezone
from jsonschema import validators
from referencing import Registry, Resource

logger = logging.getLogger(__name__)


class NoNetwork:
    def __call__(self, uri: str):
        raise RuntimeError(f"Remote $ref blocked (not preloaded): {uri}")


schema_base_uris = {
    "ieee": "https://w3id.org/ieee/ieee-1752-schema",
    "omh": "https://w3id.org/openmhealth/schemas/omh",
}


@lru_cache
def code_to_schema(code: str) -> dict[str, str]:
    """
    Transform a codeable concept string ('omh:heart-rate:1.0')

    to a valid json schema {"$ref": "https://w3id.org/openmhealth/schemas/omh/heart-rate-1.0.json"}
    """
    ns, name, version = code.split(":")
    base_uri = schema_base_uris[ns]
    return {"$ref": f"{base_uri}/{name}-{version}.json"}


def _preload_dir(reg: Registry, base_url: str, directory: Path) -> Registry:
    for p in directory.rglob("*.json"):
        url = f"{base_url}/{p.name}"  # e.g. https://w3id.org/ieee/ieee-1752-schema/header-1.0.json
        resource = Resource.from_contents(json.loads(p.read_text()))
        reg = reg.with_resource(url, resource)
        # if resource has its own id, use that, too
        res_id = resource.id()
        if res_id and res_id != url:
            # load under its self-identified id as well
            reg = reg.with_resource(res_id, resource)
    return reg


@lru_cache
def build_schema_registry() -> Registry:

    reg = Registry()
    for name in ("omh", "ieee"):
        schema_base = schema_base_uris[name]
        reg = _preload_dir(reg, schema_base, settings.DATA_DIR_PATH.schemas_dir / name)

    return reg.crawl()


def validate_with_registry(*, instance, schema, forbid_unknown_network: bool = True):
    Validator = validators.validator_for(schema)
    Validator.check_schema(schema)

    preloaded = build_schema_registry()
    registry = preloaded.combine(Registry(retrieve=NoNetwork())) if forbid_unknown_network else preloaded
    Validator(schema, registry=registry).validate(instance)


def generate_observation_value_attachment_data(coding_code):
    data_point = settings.DATA_DIR_PATH.examples_data_point / (
        coding_code.replace(":", "_").replace(".", "-") + ".json"
    )
    if not data_point.exists():
        # No example on disk: return a structurally valid OMH data point (a dict with a
        # schema-valid header derived from the code and an empty body) rather than a bare string,
        # so a caller's Observation.clean() surfaces a clear body-schema ValidationError instead of
        # an opaque "'str' object has no attribute 'get'" AttributeError.
        logger.warning("No OMH example data point for %s; using an empty placeholder body.", coding_code)
        namespace, _, remainder = coding_code.partition(":")
        name, _, version = remainder.rpartition(":")
        return {
            "header": {
                "uuid": str(uuid4()),
                "schema_id": {"namespace": namespace, "name": name, "version": version},
                "source_creation_date_time": timezone.localtime(timezone.now()).replace(microsecond=0).isoformat(),
                "modality": "sensed",
            },
            "body": {},
        }

    placeholder = json.loads(data_point.read_text())

    placeholder.get("header")["uuid"] = str(uuid4())
    placeholder.get("header")["source_creation_date_time"] = (
        timezone.localtime(timezone.now()).replace(microsecond=0).isoformat()
    )

    body = placeholder.get("body")
    for key in ("body_temperature", "oxygen_saturation", "respiratory_rate"):
        field = body.get(key)
        if field and "value" in field:
            field["value"] += random.randint(1, 10)

    body["effective_time_frame"] = {
        "date_time": timezone.localtime(timezone.now() + timedelta(hours=1)).replace(microsecond=0).isoformat()
    }
    return placeholder
