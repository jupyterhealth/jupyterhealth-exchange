# accounts/signals.py

from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from core.models import Practitioner

User = get_user_model()

@receiver(pre_save, sender=User)
def before_superuser_created(sender, instance, **kwargs):
    if instance._state.adding and instance.is_superuser and not instance.user_type:
        print(f"signals pre_save: superuser {instance.email} - setting user_type=practitioner")
        instance.user_type = "practitioner"

@receiver(post_save, sender=User)
def on_superuser_created(sender, instance, created, **kwargs):
    if created and instance.is_superuser:
        print(f"signals post_save: superuser {instance.email} - adding Practitioner")
        Practitioner.objects.create(
            jhe_user=instance
        )
