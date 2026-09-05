"""Boto3 wrapper for reading raw Oura payloads from MinIO / S3.

Minimal port: just enough surface area for the ``raw`` mode of
``manage.py ow_poll``. All settings are read from ``JheSetting`` so an
operator can repoint the pipeline at runtime without restarting Django.

The S3 endpoint, credentials, and bucket name are deliberately not
defaulted - those are environment-specific (and in the case of the
credentials, secret) so operators must set them explicitly via JheSettings
before enabling raw-mode ingest. ``ow.s3.key_prefix`` is the one knob that
keeps a default since it is an operational layout convention, not a
credential or globally-unique resource name.
"""

import json
import logging
from datetime import datetime
from typing import NamedTuple

import boto3
import requests

from core.services.jhe_settings import get_setting

logger = logging.getLogger(__name__)


class S3ObjectInfo(NamedTuple):
    key: str
    last_modified: datetime


def _required_setting(key: str) -> str:
    value = get_setting(key)
    if not value:
        raise RuntimeError(f"OW raw ingest requires JheSetting '{key}' to be set.")
    return value


def get_client():
    """Build a boto3 S3 client from ``ow.s3.*`` JheSettings."""
    return boto3.client(
        "s3",
        endpoint_url=_required_setting("ow.s3.endpoint_url"),
        aws_access_key_id=_required_setting("ow.s3.access_key_id"),
        aws_secret_access_key=_required_setting("ow.s3.secret_access_key"),
    )


def list_new_objects(ow_user_id: str, since: datetime) -> list[S3ObjectInfo]:
    """List S3 objects belonging to ``ow_user_id`` newer than ``since``.

    Filters by ``"/{ow_user_id}/"`` substring match on the S3 key (matches
    Open Wearables' default key layout: ``<prefix>/<provider>/<endpoint>/<user_id>/<...>``).
    """
    client = get_client()
    bucket = _required_setting("ow.s3.bucket_name")
    prefix = get_setting("ow.s3.key_prefix", "raw-payloads/oura/api_response")

    results: list[S3ObjectInfo] = []
    continuation_token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            last_modified = obj["LastModified"]
            if f"/{ow_user_id}/" not in key:
                continue
            if last_modified <= since:
                continue
            results.append(S3ObjectInfo(key=key, last_modified=last_modified))
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")
    return results


def read_object(key: str) -> dict:
    """Download an S3 object and parse its JSON body."""
    client = get_client()
    bucket = _required_setting("ow.s3.bucket_name")
    response = client.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read())


def revoke_connection_if_fully_unconsented(patient, study_ids):
    """Best-effort Oura disconnect in OW once a study has no consented scope left for the patient."""
    from core.models import StudyPatientScopeConsent  # noqa: PLC0415 -- avoids a models<->services import cycle

    jhe_user = patient.jhe_user
    if not jhe_user.identifier or not jhe_user.identifier.startswith("ow:"):
        return
    for study_id in study_ids:
        if StudyPatientScopeConsent.objects.filter(
            study_patient__study_id=study_id, study_patient__patient_id=patient.id, consented=True
        ).exists():
            continue
        ow_user_id = jhe_user.identifier.removeprefix("ow:")
        ow_api_url = get_setting("ow.api_url", "")
        ow_api_key = get_setting("ow.api_key", "")
        if not ow_api_url or not ow_api_key:
            logger.warning("Cannot revoke OW connection: OW integration not configured")
            return
        try:
            resp = requests.delete(
                f"{ow_api_url}/api/v1/users/{ow_user_id}/connections/oura",
                headers={"X-Open-Wearables-API-Key": ow_api_key},
                timeout=10,
            )
            if resp.status_code < 300:
                logger.info("Revoked OW connection for user %s (study %s)", ow_user_id, study_id)
            else:
                logger.warning("OW revoke returned %s: %s", resp.status_code, resp.text)
        except requests.RequestException as e:
            logger.warning("Failed to revoke OW connection: %s", e)
