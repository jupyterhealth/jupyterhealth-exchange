import logging

import requests
from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

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
