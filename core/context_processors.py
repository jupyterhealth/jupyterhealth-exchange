import json
import logging
from functools import lru_cache

from django.conf import settings
from core.permissions import ROLE_PERMISSIONS
from oauth2_provider.models import get_application_model

from core.models import DataSource, JheSetting, Organization

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_oidc_client_id():
    try:
        Application = get_application_model()
        return Application.objects.order_by("id").values_list("client_id", flat=True).first() or ""
    except Exception as exc:
        logger.warning("Unable to load the default OAuth2 client ID from the database: %s", exc)
        return ""


def constants(request):

    return {
        "JHE_VERSION": settings.JHE_VERSION,
        "SITE_TITLE": settings.SITE_TITLE,
        "SITE_URL": settings.SITE_URL,
        "OIDC_CLIENT_AUTHORITY": settings.OIDC_CLIENT_AUTHORITY,
        "OIDC_CLIENT_ID": _get_oidc_client_id(),
        "OIDC_CLIENT_REDIRECT_URI": settings.OIDC_CLIENT_REDIRECT_URI,
        "SAML2_ENABLED": settings.SAML2_ENABLED,
        "ORGANIZATION_TYPES": json.dumps(Organization.ORGANIZATION_TYPES),
        "DATA_SOURCE_TYPES": json.dumps(DataSource.DATA_SOURCE_TYPES),
        "JHE_SETTING_VALUE_TYPES": json.dumps(JheSetting.JHE_SETTING_VALUE_TYPES),
        "PATIENT_AUTHORIZATION_CODE_CHALLENGE": settings.PATIENT_AUTHORIZATION_CODE_CHALLENGE,
        "PATIENT_AUTHORIZATION_CODE_VERIFIER": settings.PATIENT_AUTHORIZATION_CODE_VERIFIER,
        "ROLE_PERMISSIONS": json.dumps(ROLE_PERMISSIONS),
    }
