from django.db import models

from .practitioner import PractitionerOrganization


class InviteCode(models.Model):
    code = models.CharField(unique=True)
    enabled = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now=True)
    label = models.CharField()

    organizations = models.ManyToManyField(
        "Organization", through="InviteCodeOrganization", related_name="invite_codes"
    )

    def clean(self):
        # invite codes are case-insensitive
        self.code = self.code.lower()
        return super().clean()

    def __str__(self):
        return self.label or f"InviteCode {self.pk}"


class InviteCodeOrganization(models.Model):
    # identical to PractitionerOrganization
    ROLE_CHOICES = PractitionerOrganization.ROLE_CHOICES

    invite_code = models.ForeignKey(InviteCode, on_delete=models.CASCADE, related_name="organization_links")
    organization = models.ForeignKey("Organization", on_delete=models.CASCADE, related_name="invite_code_links")

    role = models.TextField(
        choices=list(PractitionerOrganization.ROLE_CHOICES.items()), default=PractitionerOrganization.ROLE_MEMBER
    )

    def __str__(self):
        return f"{self.invite_code} : {self.organization} : {self.role}"
