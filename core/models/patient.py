from urllib.parse import urlparse

from django.conf import settings
from django.db import models
from django.db.utils import IntegrityError
from django.shortcuts import get_object_or_404

from core.admin_pagination import PaginatedRawQuerySet
from core.jhe_settings.service import get_setting

from .codeable_concept import CodeableConcept
from .practitioner import Practitioner


class Patient(models.Model):
    """
    Instead of using a ForeignKey and letting Django create the table we are using a OneToOneField to create a 1:1
    relationship with our JheUser model.
    jhe_user = models.ForeignKey(JheUser, unique=True, on_delete=models.CASCADE)
    """

    jhe_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="patient_profile",
        null=True,  # allows pre-existing patients without a JHE user,
        blank=True,
    )
    identifier = models.CharField(null=True)
    name_family = models.CharField()
    name_given = models.CharField()
    birth_date = models.DateField()
    telecom_phone = models.CharField(null=True)
    last_updated = models.DateTimeField(auto_now=True)
    organizations = models.ManyToManyField("Organization", through="PatientOrganization", related_name="patients")

    def __str__(self):
        return f"{self.name_family}, {self.name_given}"

    def consolidated_consented_scopes(self):
        q = """
            SELECT DISTINCT(core_codeableconcept.*)
            FROM core_codeableconcept
            JOIN core_studypatientscopeconsent ON core_studypatientscopeconsent.scope_code_id=core_codeableconcept.id
            JOIN core_studypatient ON core_studypatient.id=core_studypatientscopeconsent.study_patient_id
            WHERE core_studypatientscopeconsent.consented IS TRUE
            AND core_studypatient.patient_id=%(patient_id)s
            """

        return CodeableConcept.objects.raw(q, {"patient_id": self.id})

    @staticmethod
    def for_practitioner_organization_study(
        jhe_user_id,
        organization_id=None,
        study_id=None,
        patient_id=None,
        patient_identifier_value=None,
    ):
        organization_sql_where = f"AND core_organization.id={int(organization_id)}" if organization_id else ""
        study_sql_where = f"AND core_study.id={int(study_id)}" if study_id else ""
        patient_id_sql_where = f"AND core_patient.id={int(patient_id)}" if patient_id else ""
        patient_identifier_value_sql_where = (
            "AND core_patient.identifier=%(patient_identifier_value)s" if patient_identifier_value else ""
        )
        sql = f"""
            SELECT DISTINCT core_patient.*
            FROM core_patient
            LEFT JOIN core_studypatient
              ON core_studypatient.patient_id = core_patient.id
            LEFT JOIN core_study
              ON core_study.id = core_studypatient.study_id
            JOIN core_patientorganization
              ON core_patientorganization.patient_id = core_patient.id
            JOIN core_organization
              ON core_organization.id = core_patientorganization.organization_id
            JOIN core_practitionerorganization
              ON core_practitionerorganization.organization_id = core_organization.id
            JOIN core_practitioner
              ON core_practitioner.id = core_practitionerorganization.practitioner_id
            WHERE core_practitioner.jhe_user_id = %(jhe_user_id)s
              {organization_sql_where}
              {study_sql_where}
              {patient_id_sql_where}
              {patient_identifier_value_sql_where}
        """

        params = {"jhe_user_id": jhe_user_id}
        if patient_identifier_value:
            params["patient_identifier_value"] = patient_identifier_value
        return Patient.objects.raw(sql, params)

    @staticmethod
    def construct_invitation_link(invitation_url, client_id, auth_code, code_verifier):
        site_url = get_setting("site.url", settings.SITE_URL)
        # Use netloc (host:port) instead of hostname (host only) so the
        # consuming app can reach JHE on non-standard ports (e.g. localhost:8000).
        parsed = urlparse(site_url)
        host = parsed.netloc or parsed.hostname
        invitation_code = f"{host}~{client_id}~{auth_code}~{code_verifier}"
        return invitation_url.replace("CODE", invitation_code)

    @staticmethod
    def practitioner_authorized(
        jhe_user_id,
        patient_id=None,
        patient_identifier_system=None,
        patient_identifier_value=None,
        organization_id=None,
    ):
        qs = Patient.for_practitioner_organization_study(
            jhe_user_id,
            organization_id,
            None,
            patient_id,
            patient_identifier_value,
        )
        # this is how we limit query to at most one result
        qs = PaginatedRawQuerySet.from_raw(qs)[:1]
        return len(qs) > 0

    @staticmethod
    def for_study(jhe_user_id, study_id):
        q = """
            SELECT core_patient.*
            FROM core_patient
            JOIN core_studypatient ON core_studypatient.patient_id=core_patient.id
            JOIN core_study ON core_study.id=core_studypatient.study_id
            JOIN core_organization ON core_organization.id=core_study.organization_id
            JOIN core_patientorganization ON core_patientorganization.organization_id=core_organization.id
            WHERE core_patientorganization.jhe_user_id=%(jhe_user_id)s AND core_study.id=%(study_id)s
            """
        return Patient.objects.raw(q, {"jhe_user_id": jhe_user_id, "study_id": study_id})

    @staticmethod
    def from_jhe_user_id(jhe_user_id):
        return Patient.objects.get(jhe_user_id=jhe_user_id)

    # GET /Patient?_has:Group:member:_id=<group-id>
    @staticmethod
    def fhir_search(
        jhe_user_id,
        study_id=None,
        patient_identifier_system=None,
        patient_identifier_value=None,
    ):
        practitioner = get_object_or_404(Practitioner, jhe_user_id=jhe_user_id)
        practitioner_id = practitioner.id

        # Explicitly cast to ints so no injection vulnerability
        study_sql_where = ""
        if study_id:
            study_sql_where = f"AND core_studypatient.study_id={int(study_id)}"

        patient_identifier_value_sql_where = ""
        if patient_identifier_value:
            patient_identifier_value_sql_where = "AND core_patient.identifier=%(patient_identifier_value)s"

        # TBD: Query optimization: https://stackoverflow.com/a/6037376
        # TBD: sub constants from config
        q = """
            SELECT  'Patient' as resource_type,
                    core_patient.id as id,
                    core_patient.id::varchar as id_string,
                    -- ('{SITE_URL}/fhir/r5/Patient/' || core_patient.id) as full_url,

                    json_build_object(
                        'last_updated', core_patient.last_updated
                    )::jsonb as meta,

                    json_build_array(
                        json_build_object(
                            'value', core_patient.identifier,
                            'system', 'http://tcp.org'
                        )
                    )::jsonb as identifier,

                    json_build_array(
                        json_build_object(
                            'family', core_patient.name_family,
                            'given',    json_build_array(
                                            core_patient.name_given
                                        )
                        )
                    )::jsonb as name,

                    core_patient.birth_date as birth_date,

                    json_build_array(
                        json_build_object(
                            'value', patient_user.email,
                            'system', 'email'
                        ),
                        json_build_object(
                            'value', core_patient.telecom_phone,
                            'system', 'phone'
                        )
                    )::jsonb as telecom

            FROM core_patient
            JOIN core_jheuser AS patient_user ON patient_user.id=core_patient.jhe_user_id
            JOIN core_studypatient ON core_studypatient.patient_id=core_patient.id
            JOIN core_practitionerorganization
            ON core_practitionerorganization.organization_id = core_organization.id
            WHERE core_practitionerorganization.practitioner_id = %(practitioner_id)s

            {study_sql_where}
            {patient_identifier_value_sql_where}
            ORDER BY core_patient.name_family
            """.format(
            SITE_URL=get_setting("site.url", settings.SITE_URL),
            study_sql_where=study_sql_where,
            patient_identifier_value_sql_where=patient_identifier_value_sql_where,
        )

        records = Patient.objects.raw(
            q,
            {
                "practitioner_id": practitioner_id,
                "patient_identifier_value": patient_identifier_value,
            },
        )
        return records

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


"""
    Allows for a many-to-many relationship between organizations and patient users
"""


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
