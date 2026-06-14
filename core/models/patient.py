from urllib.parse import urlparse

from django.conf import settings
from django.db import models
from django.db.utils import IntegrityError

from core.fhir.scope import authorize_practitioner_scope, resolve_fhir_user
from core.services.jhe_settings import get_setting

from .codeable_concept import CodeableConcept


class Patient(models.Model):
    jhe_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="patient_profile",
        null=True,  # allows pre-existing patients without a JHE user,
        blank=True,
    )
    name_family = models.CharField(null=True)
    name_given = models.CharField(null=True)
    birth_date = models.DateField(null=True)
    telecom_phone = models.CharField(null=True)
    last_updated = models.DateTimeField(auto_now=True)
    organizations = models.ManyToManyField("Organization", through="PatientOrganization", related_name="patients")

    def __str__(self):
        return f"{self.name_family}, {self.name_given}"

    def consolidated_consented_scopes(self):
        # Return the distinct scope codes this patient has actively consented to, across every
        # study they are enrolled in. The traversal walks CodeableConcept ->
        # StudyPatientScopeConsent -> StudyPatient -> Patient, keeping only consent rows whose
        # "consented" flag is true; both lookups share the "studypatientscopeconsent" prefix
        # and live in one filter() call so they match the SAME consent row. distinct()
        # collapses the duplicate code rows produced when the same scope is consented across
        # multiple studies.
        return CodeableConcept.objects.filter(
            studypatientscopeconsent__consented=True,
            studypatientscopeconsent__study_patient__patient=self,
        ).distinct()

    @staticmethod
    def for_practitioner_organization_study(
        jhe_user_id,
        organization_id=None,
        study_id=None,
        patient_id=None,
        patient_identifier_value=None,
    ):
        # Return the patients a practitioner is allowed to see: every patient who shares
        # an organization with the practitioner identified by jhe_user_id. The traversal
        # walks Patient -> PatientOrganization -> Organization -> PractitionerOrganization
        # -> Practitioner -> JheUser, so a patient matches only when some organization
        # they belong to also has the practitioner as a member. The organization
        # membership of the patient and the practitioner are matched against the SAME
        # organization by keeping both lookups in one filter() call (Django reuses the
        # join for lookups sharing the "organizations" prefix within a single filter);
        # an optional organization_id narrows that shared organization. The result is then
        # optionally narrowed to patients enrolled in a given study, to a single patient by
        # id, or to a patient with a matching identifier. distinct() collapses the duplicate
        # patient rows produced by spanning these many-to-many relationships.
        organization_filters = {"organizations__practitioners__jhe_user_id": jhe_user_id}
        if organization_id:
            organization_filters["organizations__id"] = organization_id

        qs = Patient.objects.filter(**organization_filters)
        if study_id:
            qs = qs.filter(studypatient__study_id=study_id)
        if patient_id:
            qs = qs.filter(id=patient_id)
        if patient_identifier_value:
            qs = qs.filter(identifiers__value=patient_identifier_value)
        # order_by keeps pagination stable (DRF warns on an unordered paginated list).
        return qs.distinct().order_by("id")

    @staticmethod
    def construct_invitation_link(invitation_url, client_id, auth_code):
        site_url = get_setting("site.url", settings.SITE_URL)
        # Use netloc (host:port) instead of hostname (host only) so the
        # consuming app can reach JHE on non-standard ports (e.g. localhost:8000).
        parsed = urlparse(site_url)
        host = parsed.netloc or parsed.hostname
        invitation_code = f"{host}~{client_id}~{auth_code}"
        return invitation_url.replace("CODE", invitation_code)

    @staticmethod
    def practitioner_authorized(
        jhe_user_id,
        patient_id=None,
        patient_identifier_system=None,
        patient_identifier_value=None,
        organization_id=None,
    ):
        return Patient.for_practitioner_organization_study(
            jhe_user_id,
            organization_id,
            None,
            patient_id,
            patient_identifier_value,
        ).exists()

    @staticmethod
    def for_study(jhe_user_id, study_id):
        # Return the patients enrolled in a given study, but only when the practitioner
        # identified by jhe_user_id is authorized for that study. Authorization here flows
        # through the study's own organization (not the patient's): the traversal walks
        # Patient -> StudyPatient -> Study -> Organization -> PractitionerOrganization ->
        # Practitioner -> JheUser. Both lookups share the "studypatient__study" prefix and
        # live in one filter() call so Django reuses the join, ensuring the enrolled study
        # and the practitioner-authorized study are the SAME study. distinct() collapses the
        # duplicate patient rows produced by spanning these many-to-many relationships.
        return (
            Patient.objects.filter(
                studypatient__study_id=study_id,
                studypatient__study__organization__practitioners__jhe_user_id=jhe_user_id,
            )
            .distinct()
            .order_by("id")
        )

    @staticmethod
    def from_jhe_user_id(jhe_user_id):
        return Patient.objects.get(jhe_user_id=jhe_user_id)

    @staticmethod
    def fhir_search(
        jhe_user_id,
        resource_id=None,
        organization_id=None,
        study_id=None,
        patient_id=None,
        **params,
    ):
        # Return the Patients visible to the user as a queryset of Patient instances (the
        # serializer renders them into FHIR JSON). A patient user sees only themselves and the
        # organization/study/patient filters are ignored; a practitioner sees patients who
        # share one of their organizations -- narrowed by the explicit organization/study/
        # patient filters (each authorized up front, 403 on mismatch) and, via **params, by
        # identifier. resource_id selects a single patient. jhe_user is selected and
        # identifiers prefetched so the serializer's traversal does not issue N+1 queries.
        patient_identifier_value = params.get("patient_identifier_value")

        user = resolve_fhir_user(jhe_user_id)
        if user.is_patient():
            qs = Patient.objects.filter(jhe_user_id=jhe_user_id)
        else:
            authorize_practitioner_scope(jhe_user_id, organization_id, study_id, patient_id)
            qs = Patient.for_practitioner_organization_study(
                jhe_user_id, organization_id, study_id, patient_id, patient_identifier_value
            )

        if resource_id:
            qs = qs.filter(id=resource_id)

        return qs.select_related("jhe_user").prefetch_related("identifiers").distinct().order_by("name_family")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if hasattr(self, "_organization_id") and self._organization_id:
            try:
                PatientOrganization.objects.get_or_create(patient=self, organization_id=self._organization_id)
            except IntegrityError as e:
                print(f"IntegrityError: {e}")

    def __init__(self, *args, **kwargs):
        # Remove organization_id if it's passed in, as it should be handled by the M2M relationship
        self._organization_id = None
        if "organization_id" in kwargs:
            self._organization_id = kwargs.pop("organization_id")
        super().__init__(*args, **kwargs)
        self.telecom_email = None


class PatientOrganization(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="organization_links")
    organization = models.ForeignKey("Organization", on_delete=models.CASCADE, related_name="patient_links")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["patient_id", "organization_id"],
                name="core_patientorganization_unique_patient_id_organization_id",
            )
        ]


class PatientIdentifier(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="identifiers")
    system = models.CharField(db_index=True)
    value = models.CharField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["system", "value"],
                name="core_patientidentifier_unique_system_value",
            )
        ]
