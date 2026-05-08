from django.conf import settings
from django.db import models
from django.shortcuts import get_object_or_404

from core.admin_pagination import PaginatedRawQuerySet

from .codeable_concept import CodeableConcept
from .data_source import DataSource
from .practitioner import Practitioner


class Study(models.Model):
    name = models.CharField()
    description = models.TextField()
    organization = models.ForeignKey("Organization", on_delete=models.CASCADE)
    patients = models.ManyToManyField("Patient", through="StudyPatient")
    icon_url = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.name or f"Study {self.pk}"

    @staticmethod
    def for_practitioner_organization(jhe_user_id, organization_id=None, study_id=None):
        practitioner = get_object_or_404(Practitioner, jhe_user_id=jhe_user_id)
        practitioner_id = practitioner.id

        study_sql_where = f"AND core_study.id = {int(study_id)}" if study_id else ""
        organization_sql_where = f"AND core_organization.id = {int(organization_id)}" if organization_id else ""

        sql = f"""
            SELECT DISTINCT core_study.*, core_organization.*
            FROM core_study
            JOIN core_organization
              ON core_organization.id = core_study.organization_id
            JOIN core_practitionerorganization
              ON core_practitionerorganization.organization_id = core_organization.id
            WHERE core_practitionerorganization.practitioner_id = %(practitioner_id)s
            {study_sql_where}
            {organization_sql_where}
            ORDER BY core_study.name
        """
        return Study.objects.raw(sql, {"practitioner_id": practitioner_id})

    @staticmethod
    def practitioner_authorized(practitioner_user_id, study_id):
        qs = Study.for_practitioner_organization(practitioner_user_id, None, study_id)
        qs = PaginatedRawQuerySet.from_raw(qs)[:1]
        return len(qs) > 0

    def has_patient(study_id, patient_id):
        study_patients = StudyPatient.objects.filter(study_id=study_id, patient_id=patient_id)
        if len(study_patients) == 0:
            return False
        return True

    @staticmethod
    def studies_with_scopes(patient_id, pending=False):
        sql_scope_code = "NOT NULL"
        if pending:
            sql_scope_code = "NULL"

        # noqa
        q = f"""
            SELECT
                core_study.id,
                core_studyscoperequest.scope_code_id as scope_code_id,
                core_codeableconcept.coding_system as code_coding_system,
                core_codeableconcept.coding_code as code_coding_code,
                core_codeableconcept.text as code_text,
                core_studypatientscopeconsent.consented,
                core_studypatientscopeconsent.consented_time
            FROM core_studyscoperequest
            JOIN core_codeableconcept ON core_codeableconcept.id=core_studyscoperequest.scope_code_id
            JOIN core_study ON core_study.id=core_studyscoperequest.study_id
            JOIN core_studypatient ON core_studypatient.study_id=core_study.id
          LEFT JOIN core_studypatientscopeconsent ON core_studypatientscopeconsent.study_patient_id=core_studypatient.id
                AND core_studypatientscopeconsent.scope_code_id=core_studyscoperequest.scope_code_id
  WHERE core_studypatientscopeconsent.scope_code_id IS {sql_scope_code} AND core_studypatient.patient_id=%(patient_id)s;
            """

        studies_with_scopes = Study.objects.raw(q, {"patient_id": patient_id, "sql_scope_code": sql_scope_code})

        study_id_studies_map = {}

        # this will never be large
        for study_with_scope in studies_with_scopes:
            if not study_with_scope.id in study_id_studies_map:  # noqa
                study_id_studies_map[study_with_scope.id] = Study.objects.get(pk=study_with_scope.id)
                study_id_studies_map[study_with_scope.id].data_sources = DataSource.data_sources_with_scopes(
                    None, study_with_scope.id
                )
            scope_consent = {
                "code": {
                    "id": study_with_scope.scope_code_id,
                    "coding_system": study_with_scope.code_coding_system,
                    "coding_code": study_with_scope.code_coding_code,
                    "text": study_with_scope.code_text,
                },
                "consented": study_with_scope.consented,
                "consented_time": study_with_scope.consented_time,
            }
            if pending:
                study_id_studies_map[study_with_scope.id].pending_scope_consents.append(scope_consent)
            else:
                study_id_studies_map[study_with_scope.id].scope_consents.append(scope_consent)

        return list(study_id_studies_map.values())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending_scope_consents = []
        self.scope_consents = []
        self.data_sources = []


class StudyPatient(models.Model):
    study = models.ForeignKey(Study, on_delete=models.CASCADE)
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["study_id", "patient_id"],
                name="core_studypatient_unique_study_id_patient_id",
            )
        ]


class StudyPatientScopeConsent(models.Model):
    study_patient = models.ForeignKey(StudyPatient, on_delete=models.CASCADE)
    scope_actions = models.CharField(
        null=True,
        blank=False,
        # https://build.fhir.org/ig/HL7/smart-app-launch/scopes-and-launch-context.html#scopes-for-requesting-fhir-resources
        default="rs",
    )
    scope_code = models.ForeignKey("CodeableConcept", on_delete=models.CASCADE)
    consented = models.BooleanField(null=False, blank=False)
    consented_time = models.DateTimeField()

    @staticmethod
    def patient_scopes(jhe_user_id):
        q = """
            SELECT DISTINCT core_codeableconcept.* FROM core_codeableconcept
            JOIN core_studypatientscopeconsent ON core_studypatientscopeconsent.scope_code_id=core_codeableconcept.id
            JOIN core_studypatient ON core_studypatient.id=core_studypatientscopeconsent.study_patient_id
            JOIN core_patient ON core_patient.id=core_studypatient.patient_id
            WHERE core_studypatientscopeconsent.consented IS TRUE AND core_patient.jhe_user_id=%(jhe_user_id)s;
            """

        return CodeableConcept.objects.raw(q, {{"jhe_user_id": jhe_user_id}})

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["study_patient", "scope_code"],
                name="core_studypatientscopeconsent_unique_study_patient_id_scope_code_id",
            )
        ]


class StudyScopeRequest(models.Model):
    study = models.ForeignKey(Study, on_delete=models.CASCADE)
    scope_actions = models.CharField(null=True, blank=False, default="rs")
    scope_code = models.ForeignKey("CodeableConcept", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["study", "scope_code"],
                name="core_studyscoperequest_unique_study_id_scope_code_id",
            )
        ]


class StudyDataSource(models.Model):
    study = models.ForeignKey(Study, on_delete=models.CASCADE)
    data_source = models.ForeignKey("DataSource", on_delete=models.CASCADE)


class StudyClient(models.Model):
    study = models.ForeignKey(Study, on_delete=models.CASCADE)
    client = models.ForeignKey(
        settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
        on_delete=models.CASCADE,
        related_name="studies",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["study", "client"],
                name="core_studyclient_unique_study_id_client_id",
            )
        ]
