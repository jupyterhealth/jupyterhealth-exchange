import json
import logging

import boto3
import requests
from django.conf import settings
from django.http import HttpResponseRedirect
from omh_shim import convert
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.models import CodeableConcept, DataSource, JheUser, Observation

logger = logging.getLogger(__name__)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_ow_user(request):
    """
    POST /api/v1/ow/users
    Finds or creates a user in Open Wearables.
    Uses the bearer token to identify the JHE user,
    then stores the returned OW user_id in the JHE user's identifier field.
    """
    user = request.user
    ow_api_url = settings.OW_API_URL
    ow_api_key = settings.OW_API_KEY

    if not ow_api_url or not ow_api_key:
        return Response({"error": "OW integration not configured"}, status=500)

    # Check if user already has an OW user_id stored
    if user.identifier:
        return Response({"ow_user_id": user.identifier})

    # Create user in OW
    payload = {
        "email": user.email,
        "first_name": user.first_name if user.first_name != "NONE" else None,
        "last_name": user.last_name if user.last_name != "NONE" else None,
        "external_user_id": str(user.id),
    }

    try:
        ow_response = requests.post(
            ow_api_url + "/api/v1/users",
            json=payload,
            headers={"X-Open-Wearables-API-Key": ow_api_key},
            timeout=10,
        )
    except requests.RequestException as e:
        logger.error("Failed to reach OW API: %s", e)
        return Response({"error": "Failed to reach OW API"}, status=502)

    if ow_response.status_code not in (200, 201):
        logger.error("OW API error: %s %s", ow_response.status_code, ow_response.text)
        return Response({"error": "OW API error", "detail": ow_response.text}, status=ow_response.status_code)

    ow_data = ow_response.json()
    ow_user_id = str(ow_data.get("id", ""))

    # Store OW user_id in JHE user's identifier field
    user.identifier = ow_user_id
    user.save(update_fields=["identifier"])

    return Response({"ow_user_id": ow_user_id})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_oura_auth_url(request):
    """
    GET /api/v1/ow/oauth/oura/authorize
    Passes through to the OW OAuth authorize endpoint.
    Populates user_id from the bearer token (looked up from identifier field).
    """
    user = request.user
    ow_api_url = settings.OW_API_URL
    ow_api_key = settings.OW_API_KEY

    if not ow_api_url or not ow_api_key:
        return Response({"error": "OW integration not configured"}, status=500)

    ow_user_id = user.identifier
    if not ow_user_id:
        return Response({"error": "User does not have an OW user_id"}, status=400)

    redirect_uri = request.query_params.get("redirect_uri", "")

    params = {"user_id": ow_user_id}
    if redirect_uri:
        params["redirect_uri"] = redirect_uri

    try:
        ow_response = requests.get(
            ow_api_url + "/api/v1/oauth/oura/authorize",
            params=params,
            headers={"X-Open-Wearables-API-Key": ow_api_key},
            timeout=10,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        logger.error("Failed to reach OW API: %s", e)
        return Response({"error": "Failed to reach OW API"}, status=502)

    if ow_response.status_code != 200:
        logger.error("OW OAuth error: %s %s", ow_response.status_code, ow_response.text)
        return Response({"error": "OW OAuth error", "detail": ow_response.text}, status=ow_response.status_code)

    return Response(ow_response.json())


@api_view(["GET"])
@permission_classes([AllowAny])
def oura_oauth_callback(request):
    """
    GET /api/v1/oauth/oura/callback
    Proxy for the Oura OAuth callback. Oura redirects the browser here after
    the user authorizes. We forward the request to the OW backend which
    exchanges the code for tokens, then follow its redirect response.
    """
    ow_api_url = settings.OW_API_URL
    ow_api_key = settings.OW_API_KEY

    if not ow_api_url or not ow_api_key:
        return Response({"error": "OW integration not configured"}, status=500)

    try:
        ow_response = requests.get(
            ow_api_url + "/api/v1/oauth/oura/callback",
            params=request.query_params.dict(),
            headers={"X-Open-Wearables-API-Key": ow_api_key},
            timeout=15,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        logger.error("Failed to reach OW API: %s", e)
        return Response({"error": "Failed to reach OW API"}, status=502)

    # OW callback returns a redirect (303) to success/error page or custom redirect_uri
    if ow_response.status_code in (301, 302, 303, 307, 308):
        location = ow_response.headers.get("Location", "")
        # Rewrite OW-internal redirects to go through JHE
        if location.startswith(ow_api_url):
            location = location.replace(ow_api_url, "", 1)
        return HttpResponseRedirect(location)

    if ow_response.status_code >= 400:
        logger.error("OW callback error: %s %s", ow_response.status_code, ow_response.text)
        return Response({"error": "OW callback error", "detail": ow_response.text}, status=ow_response.status_code)

    return Response(ow_response.json())


def _get_s3_client():
    """Create a boto3 S3 client for OW's MinIO instance."""
    return boto3.client(
        "s3",
        endpoint_url=settings.OW_S3_ENDPOINT_URL,
        aws_access_key_id=settings.OW_S3_ACCESS_KEY,
        aws_secret_access_key=settings.OW_S3_SECRET_KEY,
        region_name=settings.OW_S3_REGION,
    )


def _is_heart_rate_file(s3_client, bucket: str, key: str) -> bool:
    """Check S3 object metadata to determine if this is a heart rate file."""
    try:
        head = s3_client.head_object(Bucket=bucket, Key=key)
        trace_id = head.get("Metadata", {}).get("trace_id", "")
        return "heartrate" in trace_id.lower()
    except Exception:
        return False


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sync_ow_data(request):
    """
    GET /api/v1/ow/sync
    Syncs Oura heart rate data from OW's MinIO/S3 into JHE Observations.

    Uses ow_key DESC LIMIT 1 as the StartAfter marker so only new files
    are fetched on each call.
    """
    bucket = settings.OW_S3_BUCKET
    if not settings.OW_S3_ENDPOINT_URL or not bucket:
        return Response({"error": "OW S3 not configured"}, status=500)

    s3_client = _get_s3_client()
    s3_prefix = settings.OW_S3_PREFIX
    prefix = f"{s3_prefix}/oura/"

    # Resume from the last synced S3 key
    last_key = (
        Observation.objects.filter(ow_key__isnull=False).order_by("-ow_key").values_list("ow_key", flat=True).first()
    )

    list_kwargs = {"Bucket": bucket, "Prefix": prefix}
    if last_key:
        list_kwargs["StartAfter"] = last_key

    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(**list_kwargs)
    except Exception as e:
        logger.error("Failed to list MinIO objects: %s", e)
        return Response({"error": "Failed to connect to MinIO"}, status=502)

    # Get heart rate CodeableConcept
    try:
        hr_code = CodeableConcept.objects.get(coding_code="omh:heart-rate:2.0")
    except CodeableConcept.DoesNotExist:
        return Response({"error": "Heart rate CodeableConcept not found. Run seed first."}, status=500)

    oura_ds, _ = DataSource.objects.get_or_create(
        name="Oura Ring",
        defaults={"type": "personal_device"},
    )

    total_files = 0
    total_observations = 0
    total_skipped = 0
    errors = []

    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]

            if not key.endswith(".json"):
                continue

            # Filter for heart rate files via metadata
            if not _is_heart_rate_file(s3_client, bucket, key):
                continue

            total_files += 1

            # Extract OW user UUID from key path
            relative_key = key
            if key.startswith(s3_prefix + "/"):
                relative_key = key[len(s3_prefix) + 1 :]
            rel_parts = relative_key.split("/")
            if len(rel_parts) < 5:
                errors.append(f"Unexpected key format: {key}")
                continue

            ow_user_uuid = rel_parts[3]

            # Resolve patient
            try:
                jhe_user = JheUser.objects.get(identifier=ow_user_uuid)
                patient = jhe_user.patient_profile
            except JheUser.DoesNotExist:
                total_skipped += 1
                logger.debug("No JHE user with identifier=%s, skipping %s", ow_user_uuid, key)
                continue
            except Exception:
                total_skipped += 1
                continue

            if patient is None:
                total_skipped += 1
                continue

            # Download and parse the file
            try:
                s3_response = s3_client.get_object(Bucket=bucket, Key=key)
                measurements = json.loads(s3_response["Body"].read().decode("utf-8"))
            except Exception as e:
                errors.append(f"Download/parse failed for {key}: {e}")
                continue

            if not isinstance(measurements, list):
                if isinstance(measurements, dict) and isinstance(measurements.get("data"), list):
                    measurements = measurements["data"]
                else:
                    errors.append(f"Expected array in {key}, got {type(measurements).__name__}")
                    continue

            # Convert and build observation batch
            obs_batch = []
            for i, sample in enumerate(measurements):
                try:
                    omh_record = convert(
                        source="oura_raw",
                        data_type="heart_rate",
                        sample=sample,
                    )
                    obs_batch.append(
                        Observation(
                            subject_patient=patient,
                            codeable_concept=hr_code,
                            data_source=oura_ds,
                            value_attachment_data=omh_record,
                            ow_key=key,
                            status="final",
                        )
                    )
                except Exception as e:
                    errors.append(f"{key} sample {i}: {e}")

            # Bulk-create in chunks
            chunk_size = 500
            created_count = 0
            for ci in range(0, len(obs_batch), chunk_size):
                chunk = obs_batch[ci : ci + chunk_size]
                try:
                    Observation.objects.bulk_create(chunk)
                    created_count += len(chunk)
                except Exception as e:
                    errors.append(f"Bulk save {key} chunk {ci}: {e}")

            total_observations += created_count
            logger.info("Synced %d/%d from %s", created_count, len(measurements), key)

    return Response(
        {
            "files_processed": total_files,
            "files_skipped": total_skipped,
            "observations_created": total_observations,
            "errors": errors if errors else None,
        }
    )
