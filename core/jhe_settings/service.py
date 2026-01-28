from django.core.cache import cache
from core.models import JheSetting

DEFAULT_CACHE_TTL = 60  # seconds


def get_setting(key: str, default=None):
    cache_key = f"jhe_setting:{key}"
    value = cache.get(cache_key)

    if value is not None:
        return value

    try:
        setting = JheSetting.objects.get(key=key)
        value = setting.get_value()
    except JheSetting.DoesNotExist:
        value = default

    cache.set(cache_key, value, DEFAULT_CACHE_TTL)
    return value
