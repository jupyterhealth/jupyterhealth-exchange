# accounts/signals.py

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from core.context_processors import SAML2_SINGLE_SOCIAL_APP_CACHE_KEY
from core.models import Practitioner

User = get_user_model()


@receiver(post_save, sender=SocialApp)
@receiver(post_delete, sender=SocialApp)
def clear_saml2_social_app_cache(sender, **kwargs):
    # The SAML login-button gate caches the "exactly one SAML SocialApp" count
    # (context_processors._saml2_enabled). Adding or removing an IdP row must
    # flip the button at once — this is the setup guide's back-out step — rather
    # than waiting out the cache TTL.
    cache.delete(SAML2_SINGLE_SOCIAL_APP_CACHE_KEY)


@receiver(pre_save, sender=User)
def before_superuser_created(sender, instance, **kwargs):
    if instance._state.adding and instance.is_superuser and not instance.user_type:
        print(f"signals pre_save: superuser {instance.email} - setting user_type=practitioner")
        instance.user_type = "practitioner"


@receiver(post_save, sender=User)
def on_superuser_created(sender, instance, created, **kwargs):
    if created and instance.is_superuser:
        print(f"signals post_save: superuser {instance.email} - adding Practitioner")
        Practitioner.objects.create(jhe_user=instance)
