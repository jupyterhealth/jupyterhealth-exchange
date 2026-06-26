# core/utils.py
import json
import random
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from dictor import dictor  # type: ignore
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, User
from django.utils import timezone
from django_saml2_auth.errors import (
    SHOULD_NOT_CREATE_USER,
)
from django_saml2_auth.exceptions import SAMLAuthError
from django_saml2_auth.user import create_new_user, get_user, get_user_id
from django_saml2_auth.utils import run_hook
from jsonschema import validators
from referencing import Registry, Resource


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
        return "placeholder"

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


def get_or_create_user(user: dict[str, Any]) -> tuple[bool, User]:
    """Get or create a new user and optionally add it to one or more group(s)

    Args:
        user (Dict[str, Any]): User information

    Raises:
        SAMLAuthError: Cannot create user. Missing user_id.
        SAMLAuthError: Cannot create user.

    Returns:
        Tuple[bool, User]: A tuple containing user creation status and user object
    """
    saml2_auth_settings = settings.SAML2_AUTH
    user_model = get_user_model()
    created = False

    try:
        target_user = get_user(user)
    except user_model.DoesNotExist:
        should_create_new_user = dictor(saml2_auth_settings, "CREATE_USER", True)
        if should_create_new_user:
            user_id = get_user_id(user)
            if not user_id:
                raise SAMLAuthError(
                    "Cannot create user. Missing user_id.",
                    extra={
                        "error_code": SHOULD_NOT_CREATE_USER,
                        "reason": "Cannot create user. Missing user_id.",
                        "status_code": 400,
                    },
                )
            target_user = create_new_user(
                user_id,
                user["first_name"],
                user["last_name"],
                user_type="practitioner",
                identifier=user.get("user_identity", {}).get("id")[0],
            )

            create_user_trigger = dictor(saml2_auth_settings, "TRIGGER.CREATE_USER")
            if create_user_trigger:
                run_hook(create_user_trigger, user)  # type: ignore

            target_user.refresh_from_db()
            created = True
        else:
            raise SAMLAuthError(
                "Cannot create user.",
                extra={
                    "exc_type": Exception,
                    "error_code": SHOULD_NOT_CREATE_USER,
                    "reason": "Due to current config, a new user should not be created.",
                    "status_code": 500,
                },
            )

    # Optionally update this user's group assignments by updating group memberships from SAML groups
    # to Django equivalents
    group_attribute = dictor(saml2_auth_settings, "ATTRIBUTES_MAP.groups")
    group_map = dictor(saml2_auth_settings, "GROUPS_MAP")

    if group_attribute and group_attribute in user["user_identity"]:
        groups = []

        for group_name in user["user_identity"][group_attribute]:
            # Group names can optionally be mapped to different names in Django
            if group_map and group_name in group_map:
                group_name_django = group_map[group_name]
            else:
                group_name_django = group_name

            try:
                groups.append(Group.objects.get(name=group_name_django))
            except Group.DoesNotExist:
                should_create_new_groups = dictor(saml2_auth_settings, "CREATE_GROUPS", False)
                if should_create_new_groups:
                    groups.append(Group.objects.create(name=group_name_django))

        target_user.groups.set(groups)

    return (created, target_user)
