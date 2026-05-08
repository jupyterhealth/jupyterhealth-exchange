"""Boto3 wrapper for reading raw Oura payloads from MinIO / S3.

Minimal port: just enough surface area for the ``raw`` mode of
``manage.py ow_poll``. All settings are read from ``JheSetting`` so an
operator can repoint the pipeline at runtime without restarting Django.
"""

import json
from datetime import datetime
from typing import NamedTuple

import boto3

from core.jhe_settings.service import get_setting


class S3ObjectInfo(NamedTuple):
    key: str
    last_modified: datetime


def get_client():
    """Build a boto3 S3 client from ``ow.s3.*`` JheSettings."""
    return boto3.client(
        "s3",
        endpoint_url=get_setting("ow.s3.endpoint_url", "http://localhost:9000"),
        aws_access_key_id=get_setting("ow.s3.access_key_id", "minioadmin"),
        aws_secret_access_key=get_setting("ow.s3.secret_access_key", "minioadmin"),
    )


def list_new_objects(ow_user_id: str, since: datetime) -> list[S3ObjectInfo]:
    """List S3 objects belonging to ``ow_user_id`` newer than ``since``.

    Filters by ``"/{ow_user_id}/"`` substring match on the S3 key (matches
    Open Wearables' default key layout: ``<prefix>/<provider>/<endpoint>/<user_id>/<...>``).
    """
    client = get_client()
    bucket = get_setting("ow.s3.bucket_name", "raw-payloads")
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
    bucket = get_setting("ow.s3.bucket_name", "raw-payloads")
    response = client.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read())
