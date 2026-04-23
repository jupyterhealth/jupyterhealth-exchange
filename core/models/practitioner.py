from django.conf import settings
from django.db import models


class Practitioner(models.Model):
    jhe_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="practitioner_profile",
    )
    identifier = models.CharField(null=True)
    name_family = models.CharField(null=True)
    name_given = models.CharField(null=True)
    telecom_phone = models.CharField(null=True)
    last_updated = models.DateTimeField(auto_now=True)
    organizations = models.ManyToManyField(
        "Organization", through="PractitionerOrganization", related_name="practitioners"
    )

    def __str__(self):
        name = f"{self.name_given or ''} {self.name_family or ''}".strip()
        return name or f"Practitioner {self.pk}"


class PractitionerOrganization(models.Model):
    ROLE_MEMBER = "member"
    ROLE_MANAGER = "manager"
    ROLE_VIEWER = "viewer"

    ROLE_CHOICES = {
        ROLE_MEMBER: "Member",
        ROLE_MANAGER: "Manager",
        ROLE_VIEWER: "Viewer",
    }

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="organization_links")
    organization = models.ForeignKey("Organization", on_delete=models.CASCADE, related_name="practitioner_links")

    role = models.CharField(max_length=10, choices=list(ROLE_CHOICES.items()), default=ROLE_MEMBER)

    class Meta:
        unique_together = ("practitioner", "organization")
