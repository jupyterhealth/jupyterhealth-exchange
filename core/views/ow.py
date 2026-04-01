import logging

import requests
from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.models import JheSetting

logger = logging.getLogger(__name__)


def _get_ow_config():
    """Read OW base URL and API key from JheSettings."""
    base_url = None
    api_key = None

    try:
        setting = JheSetting.objects.get(key="ow.api_base_url", setting_id=None)
        base_url = setting.get_value()
    except JheSetting.DoesNotExist:
        pass

    try:
        setting = JheSetting.objects.get(key="ow.api_key", setting_id=None)
        api_key = setting.get_value()
    except JheSetting.DoesNotExist:
        pass

    if not base_url or not api_key:
        raise ValueError("Open Wearables settings missing: configure ow.api_base_url and ow.api_key in System Settings")

    return base_url.rstrip("/"), api_key


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_ow_user(request):
    """
    POST /api/v1/ow/users

    Creates (or finds) a user in Open Wearables for the authenticated JHE user.
    Stores the returned OW user_id in the JHE user's identifier field.
    """
    user = request.user

    try:
        ow_base_url, ow_api_key = _get_ow_config()
    except ValueError as e:
        return Response({"error": str(e)}, status=500)

    # If user already has an OW identifier, return it
    if user.identifier and user.identifier.startswith("ow:"):
        ow_user_id = user.identifier.replace("ow:", "", 1)
        return Response({"ow_user_id": ow_user_id, "created": False})

    payload = {
        "email": user.email,
        "external_user_id": str(user.pk),
    }
    if user.first_name:
        payload["first_name"] = user.first_name
    if user.last_name:
        payload["last_name"] = user.last_name

    try:
        resp = requests.post(
            f"{ow_base_url}/api/v1/users",
            json=payload,
            headers={"X-Open-Wearables-API-Key": ow_api_key},
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error("OW API request failed: %s", e)
        return Response({"error": "Failed to connect to Open Wearables"}, status=502)

    if resp.status_code == 201:
        ow_data = resp.json()
        ow_user_id = ow_data.get("id")
        user.identifier = f"ow:{ow_user_id}"
        user.save(update_fields=["identifier"])
        return Response({"ow_user_id": ow_user_id, "created": True}, status=201)
    elif resp.status_code == 409:
        # User already exists in OW — try to look up by email
        try:
            lookup_resp = requests.get(
                f"{ow_base_url}/api/v1/users",
                params={"email": user.email},
                headers={"X-Open-Wearables-API-Key": ow_api_key},
                timeout=30,
            )
            if lookup_resp.ok:
                users_data = lookup_resp.json()
                items = users_data.get("items", users_data.get("data", []))
                if items:
                    ow_user_id = items[0].get("id")
                    user.identifier = f"ow:{ow_user_id}"
                    user.save(update_fields=["identifier"])
                    return Response({"ow_user_id": ow_user_id, "created": False})
        except requests.RequestException:
            pass
        return Response({"error": "User already exists in OW but lookup failed"}, status=409)
    else:
        logger.error("OW user creation failed: %s %s", resp.status_code, resp.text[:300])
        return Response({"error": f"OW API error: {resp.status_code}"}, status=502)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_providers(request):
    """
    GET /api/v1/ow/providers

    Proxies to Open Wearables' provider list.
    Returns available wearable providers (Oura, Garmin, etc.) with icons.
    """
    try:
        ow_base_url, ow_api_key = _get_ow_config()
    except ValueError as e:
        return Response({"error": str(e)}, status=500)

    try:
        resp = requests.get(
            f"{ow_base_url}/api/v1/oauth/providers",
            params={"enabled_only": "true", "cloud_only": "true"},
            headers={"X-Open-Wearables-API-Key": ow_api_key},
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error("OW providers request failed: %s", e)
        return Response({"error": "Failed to connect to Open Wearables"}, status=502)

    if resp.ok:
        providers = resp.json()
        # Rewrite icon URLs to point to OW backend
        for p in providers:
            if p.get("icon_url"):
                p["icon_url"] = f"{ow_base_url}{p['icon_url']}"
        return Response(providers)
    else:
        logger.error("OW providers failed: %s %s", resp.status_code, resp.text[:300])
        return Response({"error": f"OW API error: {resp.status_code}"}, status=502)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def provider_authorize(request, provider):
    """
    GET /api/v1/ow/oauth/<provider>/authorize

    Proxies to Open Wearables' OAuth authorize endpoint for any provider.
    Populates user_id from the authenticated JHE user's stored OW identifier.
    """
    user = request.user

    if not user.identifier or not user.identifier.startswith("ow:"):
        return Response(
            {"error": "User has no Open Wearables account. Call POST /api/v1/ow/users first."},
            status=400,
        )

    ow_user_id = user.identifier.replace("ow:", "", 1)

    try:
        ow_base_url, ow_api_key = _get_ow_config()
    except ValueError as e:
        return Response({"error": str(e)}, status=500)

    # Redirect URI: where the user returns after OAuth completes
    redirect_uri = request.query_params.get("redirect_uri")
    if not redirect_uri:
        redirect_uri = request.build_absolute_uri("/ow/complete")

    params = {
        "user_id": ow_user_id,
        "redirect_uri": redirect_uri,
    }

    try:
        resp = requests.get(
            f"{ow_base_url}/api/v1/oauth/{provider}/authorize",
            params=params,
            headers={"X-Open-Wearables-API-Key": ow_api_key},
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error("OW %s authorize request failed: %s", provider, e)
        return Response({"error": "Failed to connect to Open Wearables"}, status=502)

    if resp.ok:
        return Response(resp.json())
    else:
        logger.error("OW %s authorize failed: %s %s", provider, resp.status_code, resp.text[:300])
        return Response({"error": f"OW API error: {resp.status_code}"}, status=502)


@api_view(["GET"])
@permission_classes([AllowAny])
def provider_callback_proxy(request, provider):
    """
    GET /api/v1/oauth/<provider>/callback

    Proxies the OAuth callback to Open Wearables.
    The provider redirects here (port 8000 = JHE) because that's the registered redirect_uri.
    We forward the browser to OW (port 8001) which handles the token exchange.
    """
    try:
        ow_base_url, _ = _get_ow_config()
    except ValueError as e:
        return Response({"error": str(e)}, status=500)

    query_string = request.META.get("QUERY_STRING", "")
    redirect_url = f"{ow_base_url}/api/v1/oauth/{provider}/callback"
    if query_string:
        redirect_url = f"{redirect_url}?{query_string}"

    return HttpResponseRedirect(redirect_url)
