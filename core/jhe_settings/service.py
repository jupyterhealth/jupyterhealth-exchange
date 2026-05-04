from django.core.cache import cache

DEFAULT_CACHE_TTL = 60  # seconds


def get_setting(key: str, default=None):
    cache_key = f"jhe_setting:{key}"
    value = cache.get(cache_key)

    if value is not None:
        return value

    # Lazy import to avoid circular dependency with core.models
    from core.models import JheSetting

    try:
        setting = JheSetting.objects.get(key=key)
        value = setting.get_value()
    except JheSetting.DoesNotExist:
        value = default

    cache.set(cache_key, value, DEFAULT_CACHE_TTL)
    return value


def get_saml_metadata_urls(user_id=None):
    """SAML trigger hook — called at request time, safe to use get_setting() here."""
    url = get_setting("auth.sso.idp_metadata_url")
    if url:
        return [{"url": url}]
    return []
