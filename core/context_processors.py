import json
import logging
from functools import lru_cache

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.core.cache import cache
from oauth2_provider.models import get_application_model

from core.fhir.config import supported_resource_types
from core.models import DataSource, JheSetting, Organization
from core.permissions import ROLE_PERMISSIONS
from core.services.jhe_settings import DEFAULT_CACHE_TTL, get_setting

logger = logging.getLogger(__name__)

# The "exactly one SAML SocialApp" gate cached by _saml2_enabled(). A
# post_save/post_delete receiver on SocialApp (core/signals.py) clears it so
# adding or removing an IdP row takes effect at once, not after the TTL.
SAML2_SINGLE_SOCIAL_APP_CACHE_KEY = "saml2_single_social_app"


@lru_cache(maxsize=1)
def _get_oidc_client_id():
    Application = get_application_model()
    try:
        client_id = Application.objects.filter(name="JHE Admin UI").values_list("client_id", flat=True).first()
    except Exception:
        logger.exception("Error looking up OAuth2 client ID for 'JHE Admin UI'")
        client_id = None
    if client_id is None:
        logger.error(
            "Unable to load the OAuth2 client ID for 'JHE Admin UI' from the database. Make sure it is defined in Applications via the django admin interface (/admin/)."
        )
    return client_id


def _saml2_enabled():
    # The login button's provider_login_url resolves only when exactly one saml
    # SocialApp exists (0 raises DoesNotExist, >1 MultipleObjectsReturned, either
    # of which would 500 the whole login page). Hide the button instead;
    # multi-IdP deployments need per-IdP buttons in a customized template.
    if not get_setting("auth.sso.saml2", 0):
        return False
    # Same TTL cache as get_setting (not lru_cache: SocialApp rows are runtime
    # admin config, so the two gates must go stale together, within 60s).
    enabled = cache.get(SAML2_SINGLE_SOCIAL_APP_CACHE_KEY)
    if enabled is None:
        enabled = SocialApp.objects.filter(provider="saml").count() == 1
        cache.set(SAML2_SINGLE_SOCIAL_APP_CACHE_KEY, enabled, DEFAULT_CACHE_TTL)
    return enabled


def constants(request):
    site_url = get_setting("site.url", settings.SITE_URL)

    return {
        "JHE_VERSION": settings.JHE_VERSION,
        "SITE_TITLE": get_setting("site.ui.title"),
        "SITE_URL": site_url,
        "OIDC_CLIENT_AUTHORITY_PATH": settings.OIDC_CLIENT_AUTHORITY_PATH,
        "OAUTH2_CALLBACK_PATH": settings.OAUTH2_CALLBACK_PATH,
        "OIDC_CLIENT_ID": _get_oidc_client_id(),
        "SAML2_ENABLED": _saml2_enabled(),
        "ORGANIZATION_TYPES": json.dumps(Organization.ORGANIZATION_TYPES),
        "DATA_SOURCE_TYPES": json.dumps(DataSource.DATA_SOURCE_TYPES),
        "JHE_SETTING_VALUE_TYPES": json.dumps(JheSetting.JHE_SETTING_VALUE_TYPES),
        "ROLE_PERMISSIONS": json.dumps(ROLE_PERMISSIONS),
        "FHIR_RESOURCES": json.dumps(supported_resource_types()),
    }
