from django.conf import settings
from django.db import models

from core.fhir.scope import authorize_practitioner_scope, resolve_fhir_user


class Practitioner(models.Model):
    jhe_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="practitioner_profile",
    )
    name_family = models.CharField(null=True)
    name_given = models.CharField(null=True)
    last_updated = models.DateTimeField(auto_now=True)
    organizations = models.ManyToManyField(
        "Organization", through="PractitionerOrganization", related_name="practitioners"
    )
    settings = models.JSONField(default=dict, blank=True)

    def save_setting(self, key, value):
        self.settings[key] = value
        self.save(update_fields=["settings"])

    def delete_setting(self, key):
        self.settings.pop(key, None)
        self.save(update_fields=["settings"])

    def get_setting(self, key):
        return self.settings.get(key)

    def remember_settings(self, save, forget=()):
        """Persist a page's sticky UI selections in a single write.

        ``save`` maps setting key -> selected value. A key whose value is empty is *removed*
        when it is listed in ``forget`` -- the control was explicitly cleared ("All Studies", a
        blank Patient ID box) and a stale value must not come back on the next visit -- and is
        otherwise left untouched, because the caller simply did not send that control. Nothing
        is written when nothing changed.

        Callers are the practitioner-facing list endpoints that back a browser page's filters
        (see core/views/observation.py, patient.py, study.py and fhir.py).
        """
        changed = False
        for key, value in save.items():
            if value is None or value == "":
                if key in forget and key in self.settings:
                    del self.settings[key]
                    changed = True
            elif self.settings.get(key) != value:
                self.settings[key] = value
                changed = True
        if changed:
            self.save(update_fields=["settings"])

    @staticmethod
    def fhir_search(
        jhe_user_id,
        resource_id=None,
        organization_id=None,
        study_id=None,
        patient_id=None,
        **params,
    ):
        # Return the Practitioners visible to the user as a queryset of Practitioner instances
        # (the serializer renders them into FHIR JSON). A patient user sees the practitioners in
        # the organizations they belong to and the organization/study/patient filters are
        # ignored; a practitioner sees the practitioners sharing one of their organizations --
        # narrowed to one organization, the organization backing a given study, or the
        # organizations a given patient belongs to (each explicit filter authorized up front,
        # 403 on mismatch). resource_id selects a single practitioner. distinct() collapses the
        # duplicate rows produced by spanning the organization many-to-many relationship.
        user = resolve_fhir_user(jhe_user_id)
        if user.is_patient():
            qs = Practitioner.objects.filter(organizations__patients__jhe_user_id=jhe_user_id)
        else:
            authorize_practitioner_scope(jhe_user_id, organization_id, study_id, patient_id)
            qs = Practitioner.objects.filter(organizations__practitioners__jhe_user_id=jhe_user_id)
            if organization_id:
                qs = qs.filter(organizations__id=organization_id)
            if study_id:
                qs = qs.filter(organizations__study__id=study_id)
            if patient_id:
                qs = qs.filter(organizations__patients__id=patient_id)

        if resource_id:
            qs = qs.filter(id=resource_id)

        return qs.distinct().order_by("name_family", "name_given")

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

    role = models.TextField(choices=list(ROLE_CHOICES.items()), default=ROLE_MEMBER)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["practitioner", "organization"],
                name="core_practitionerorganization_unique_practitioner_id_organization_id",
            )
        ]


class PractitionerIdentifier(models.Model):
    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="identifiers")
    system = models.CharField(db_index=True)
    value = models.CharField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["system", "value"],
                name="core_practitioneridentifier_unique_system_value",
            )
        ]
