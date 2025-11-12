import base64
import json
import logging
from datetime import timedelta

from random import SystemRandom

import humps
from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, BadRequest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMessage
from django.db import models, connection, transaction
from django.db.models import Q
from django.db.utils import IntegrityError
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext_lazy as _
from fhir.resources.observation import Observation as FHIRObservation
from fhir.resources.patient import Patient as FHIRPatient
from jsonschema import ValidationError
from core.utils import validate_with_registry
from oauth2_provider.models import AccessToken, RefreshToken, Grant, IDToken, get_application_model, get_grant_model

from .tokens import account_activation_token

logger = logging.getLogger(__name__)


class JheUserManager(BaseUserManager):
    def create_user(self, email, password=None, user_type=None, **extra_fields):
        """
        Args:
            email (str): A valid email.
            password (str): A valid password or no password for SSO users.
            user_type: Practitioner or Patient.
        """
        if not email:
            raise ValueError(_("The Email must be set"))
        email = self.normalize_email(email)
        user = self.model(email=email, user_type=user_type, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Superuser must have is_staff=True."))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Superuser must have is_superuser=True."))
        return self.create_user(email, password, **extra_fields)

    def get_by_ehr_id(self, ehr_id):
        return JheUser.objects.filter(identifier=ehr_id)


class JheUser(AbstractUser):
    username = None
    email = models.EmailField(_("Email Address"), max_length=254, unique=True)
    email_is_verified = models.BooleanField(default=False)
    identifier = models.CharField()
    USER_TYPE_CHOICES = (
        ("patient", "Patient"),
        ("practitioner", "Practitioner"),
    )
    user_type = models.CharField(max_length=12, choices=USER_TYPE_CHOICES, null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = JheUserManager()

    def __str__(self):
        return self.email

    def has_module_perms(self, app_label):
        if self.is_superuser:
            return super().has_module_perms(app_label)
        return False

    @transaction.atomic
    def delete(self, *args, **kwargs):
        """
        Not using built-in delete() because we've removed default Django user groups table from DB

        Custom delete:
        - Avoids hitting removed auth M2M tables.
        - Proactively deletes Django OAuth Toolkit artifacts that FK to this user.
        - Finally, raw-DELETE the user row.
        """
        # 1) Remove Django OAuth Toolkit artifacts referencing this user
        # (Order chosen to avoid FK surprises across Django OAuth Toolkit versions)
        IDToken.objects.filter(user=self).delete()
        Grant.objects.filter(user=self).delete()
        RefreshToken.objects.filter(user=self).delete()  # often FK→AccessToken and FK→User
        AccessToken.objects.filter(user=self).delete()

        # If you allow users to own OAuth applications, also remove those:
        Application = get_application_model()
        Application.objects.filter(user=self).delete()

        # 2) Now delete the user row itself (bypasses Django's M2M cleanup)
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM core_jheuser WHERE id = %s", [self.id])
            deleted = cursor.rowcount

        if deleted:
            return deleted
        raise ObjectDoesNotExist(f"JheUser with id={self.id} did not exist")

    def save(self, *args, **kwargs):
        is_new = (
            self._state.adding
        )  # lives on internal ModelState object; Django's built-in flag for "has this object been added to the
        # database yet?"
        super().save(*args, **kwargs)

        if is_new and self.user_type:
            if self.user_type == "patient" and not hasattr(self, "patient_profile"):
                print("JheUser save patient profile")
                Patient.objects.create(
                    jhe_user=self,
                    name_family=self.last_name or "",
                    name_given=self.first_name or "",
                    birth_date=timezone.now().date(),  # TBD, do we want a default value equivalent to this?
                    identifier=self.identifier,
                )
            elif self.user_type == "practitioner" and not hasattr(self, "practitioner_profile"):
                print("JheUser save practitioner profile")
                with transaction.atomic():
                    practitioner = Practitioner.objects.create(
                        jhe_user=self,
                        name_family=self.last_name,
                        name_given=self.first_name,
                        identifier=self.identifier,
                    )

                    # --- parse multi-org:role string from env ---
                    mapping_str = getattr(settings, "PRACTITIONER_DEFAULT_ORGS", "")
                    mapping_str = (mapping_str or "").strip()

                    if mapping_str:
                        # Expected format: "<org_id>:<role>;<org_id>:<role>"
                        parts = [p.strip() for p in mapping_str.split(";") if p.strip()]
                        if not parts:
                            raise DjangoValidationError("PRACTITIONER_DEFAULT_ORGS must be non-empty when set.")

                        valid_roles = {c[0] for c in PractitionerOrganization.ROLE_CHOICES}
                        requested: list[tuple[int, str]] = []

                        for idx, part in enumerate(parts, start=1):
                            if ":" not in part:
                                raise DjangoValidationError(
                                    f"PRACTITIONER_DEFAULT_ORGS entry #{idx} is missing ':'. "
                                    "Expected '<org_id>:<role>'."
                                )
                            org_id_str, role = [s.strip() for s in part.split(":", 1)]

                            if not org_id_str or not org_id_str.isdigit():
                                raise DjangoValidationError(
                                    f"PRACTITIONER_DEFAULT_ORGS entry #{idx} has invalid org ID "
                                    f"'{org_id_str}'. Must be a numeric ID."
                                )
                            if not role:
                                raise DjangoValidationError(
                                    f"PRACTITIONER_DEFAULT_ORGS entry #{idx} is missing a role."
                                )
                            if role not in valid_roles:
                                raise DjangoValidationError(
                                    f"PRACTITIONER_DEFAULT_ORGS entry #{idx} has invalid role '{role}'. "
                                    f"Valid roles: {sorted(valid_roles)}"
                                )

                            requested.append((int(org_id_str), role))

                        # Ensure all org IDs exist
                        org_ids = [oid for oid, _ in requested]
                        orgs = Organization.objects.filter(id__in=org_ids)
                        found_ids = {o.id for o in orgs}
                        missing = sorted(set(org_ids) - found_ids)
                        if missing:
                            raise DjangoValidationError(
                                f"PRACTITIONER_DEFAULT_ORGS references missing Organization ID(s): {missing}"
                            )

                        org_by_id = {o.id: o for o in orgs}

                        # Create/update links idempotently
                        for org_id, role in requested:
                            org = org_by_id[org_id]
                            link, created = PractitionerOrganization.objects.get_or_create(
                                practitioner=practitioner,
                                organization=org,
                                defaults={"role": role},
                            )
                            if not created and link.role != role:
                                link.role = role
                                link.save(update_fields=["role"])

    def send_email_verificaion(self):
        message = render_to_string(
            "registration/verify_email_message.html",
            {
                "site_url": settings.SITE_URL,
                "email_address": self.email,
                "user_id": urlsafe_base64_encode(force_bytes(self.id)),
                "token": account_activation_token.make_token(self),
            },
        )
        email = EmailMessage("JHE E-mail Verification", message, to=[self.email])
        email.content_subtype = "html"
        email.send()

    def is_patient(self):
        return self.user_type == "patient" or hasattr(self, "patient_profile")

    def is_practitioner(self):
        return self.user_type == "practitioner" or hasattr(self, "practitioner_profile")

    def get_patient(self):
        patient = Patient.objects.filter(jhe_user_id=self.id)
        return patient[0] if patient else None

    @property
    def practitioner(self):
        return getattr(self, "practitioner_profile", None)

    @property
    def patient(self):
        if not hasattr(self, "_patient"):
            self._patient = getattr(self, "patient_profile", None)
        return self._patient

    @patient.setter
    def patient(self, value):
        # Handle the case where value is the get_patient method instead of its result
        if value is not None and callable(value):
            value = value()

        if value is not None and not hasattr(value, "jhe_user"):
            raise BadRequest("Expected Patient object or None")
        self._patient = value

    def organization(self):
        if self.is_practitioner():
            return self.practitioner.organizations.all()
        elif self.is_patient():
            return self.patient.organizations.all()
        else:
            return None

    # https://github.com/jazzband/django-oauth-toolkit/blob/102c85141ec44549e17080c676292e79e5eb46cc/oauth2_provider/oauth2_validators.py#L675
    def create_authorization_code(self, application_id, redirect_uri):

        self.last_login = timezone.now()
        self.save()

        Grant = get_grant_model()

        Grant.objects.filter(user_id=self.id).delete()

        # https://github.com/oauthlib/oauthlib/blob/f9a07c6c07d0ddac255dd322ef5fc54a7a46366d/oauthlib/common.py#L188
        UNICODE_ASCII_CHARACTER_SET = "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789"
        authorization_code = "".join(SystemRandom().choice(UNICODE_ASCII_CHARACTER_SET) for _ in range(30))

        return Grant.objects.create(
            application_id=application_id,
            user_id=self.id,
            code=authorization_code,
            expires=timezone.now() + timedelta(seconds=settings.PATIENT_AUTHORIZATION_CODE_EXPIRE_SECONDS),
            redirect_uri=redirect_uri,
            scope="openid",
            # https://github.com/oauthlib/oauthlib/blob/f9a07c6c07d0ddac255dd322ef5fc54a7a46366d/oauthlib/oauth2/rfc6749/grant_types/authorization_code.py#L18
            code_challenge=settings.PATIENT_AUTHORIZATION_CODE_CHALLENGE,
            code_challenge_method="S256",
            nonce="",
            claims=json.dumps({}),
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Don't initialize patient here since it's a property without a setter


class Organization(models.Model):

    # https://build.fhir.org/valueset-organizations-type.html
    ORGANIZATION_TYPE_CHOICES = {
        "root": "ROOT",
        "prov": "Healthcare Provider",
        "dept": "Hospital Department",
        "team": "Organizational team",
        "govt": "Government",
        "ins": "Insurance Company",
        "pay": "Payer",
        "edu": "Educational Institute",
        "reli": "Religious Institution",
        "crs": "Clinical Research Sponsor",
        "cg": "Community Group",
        "bus": "Non-Healthcare Business or Corporation",
        "other": "Other",
        "laboratory": "Laboratory",
        "imaging": "Imaging Center",
        "pharmacy": "Pharmacy",
        "health-information-network": "Health Information Network",
        "health-data-aggregator": "Health Data Aggregator",
    }

    name = models.CharField()
    type = models.CharField(choices=ORGANIZATION_TYPE_CHOICES, null=False, blank=False)
    part_of = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)

    # Helper method to return all users in this organization
    @property
    def users(self):

        patient_user_ids = (
            PatientOrganization.objects.filter(organization=self)
            .select_related("patient__jhe_user")
            .values_list("patient__jhe_user_id", flat=True)
        )

        practitioner_user_ids = (
            PractitionerOrganization.objects.filter(organization=self)
            .select_related("practitioner__jhe_user")
            .values_list("practitioner__jhe_user_id", flat=True)
        )

        # Combine the IDs and get all of the users
        return JheUser.objects.filter(Q(id__in=patient_user_ids) | Q(id__in=practitioner_user_ids))

    @staticmethod
    def collect_children(parent):
        children = Organization.get_children(parent.id)
        for child in children:
            parent.children.append(child)
            Organization.collect_children(child)

    @staticmethod
    def get_children(parent_id):
        return Organization.objects.filter(part_of=parent_id).order_by("name")

    @staticmethod
    def for_practitioner(practitioner_user_id):
        q = """
            SELECT core_organization.*
            FROM core_organization
            JOIN core_practitionerorganization ON core_practitionerorganization.organization_id=core_organization.id
            JOIN core_practitioner ON core_practitioner.id=core_practitionerorganization.practitioner_id
            WHERE core_practitioner.jhe_user_id=%(practitioner_user_id)s
            """

        return Organization.objects.raw(q, {"practitioner_user_id": practitioner_user_id})

    @staticmethod
    def for_patient(patient_user_id):
        q = """
            SELECT core_organization.*
            FROM core_organization
            JOIN core_patientorganization ON core_patientorganization.organization_id=core_organization.id
            JOIN core_patient ON core_patient.id=core_patientorganization.patient_id
            WHERE core_patient.jhe_user_id=%(patient_user_id)s
            """

        return Organization.objects.raw(q, {"patient_user_id": patient_user_id})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.children = []


class PractitionerQuerySet(models.QuerySet):
    def count_studies(self, jhe_user_id, organization_id=None, study_id=None):
        """
        Returns the total number of distinct studies for this practitioner,
        optionally filtered by organization_id and/or study_id.
        """
        params = {"jhe_user_id": jhe_user_id}
        study_clause = ""
        if study_id:
            study_clause = "AND core_study.id = %(study_id)s"
            params["study_id"] = int(study_id)
        org_clause = ""
        if organization_id:
            org_clause = "AND core_organization.id = %(org_id)s"
            params["org_id"] = int(organization_id)

        sql = f"""
            SELECT COUNT(DISTINCT core_study.id) AS count
            FROM core_study
            JOIN core_organization
              ON core_organization.id = core_study.organization_id
            JOIN core_practitionerorganization
              ON core_practitionerorganization.organization_id = core_organization.id
            WHERE core_practitioner.jhe_user_id = %(jhe_user_id)s
            {study_clause}
            {org_clause}
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return row[0] if row else 0


class PractitionerManager(models.Manager):
    def get_queryset(self):
        return PractitionerQuerySet(self.model, using=self._db)

    def count_studies(self, *args, **kwargs):
        return self.get_queryset().count_studies(*args, **kwargs)


class Practitioner(models.Model):
    jhe_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="practitioner_profile",
    )
    identifier = models.CharField(null=True)
    name_family = models.CharField(null=True)
    name_given = models.CharField(null=True)
    birth_date = models.DateField(null=True)
    telecom_phone = models.CharField(null=True)
    last_updated = models.DateTimeField(default=timezone.now)
    organizations = models.ManyToManyField(
        Organization, through="PractitionerOrganization", related_name="practitioners"
    )
    objects = PractitionerManager()


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
    last_updated = models.DateTimeField(default=timezone.now)
    organizations = models.ManyToManyField(Organization, through="PatientOrganization", related_name="patients")

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
        page=1,
        pageSize=20,
    ):
        pageSize = int(pageSize) if pageSize is not None else 20
        page = int(page) if page is not None else 1
        offset = pageSize * (page - 1)
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
            LIMIT {pageSize}
            OFFSET {offset};
        """

        params = {"jhe_user_id": jhe_user_id}
        if patient_identifier_value:
            params["patient_identifier_value"] = patient_identifier_value
        return Patient.objects.raw(sql, params)

    # TODO: need to make this as DRY as possible, and re-use in other models.
    @staticmethod
    def count_for_practitioner_organization_study(
        jhe_user_id,
        organization_id=None,
        study_id=None,
        patient_id=None,
        patient_identifier_value=None,
    ):
        """
        Count patients a practitioner is allowed to see (FHIR _total).
        """
        practitioner = Practitioner.objects.get(jhe_user_id=jhe_user_id)
        practitioner_id = practitioner.id

        organization_sql_where = ""
        if organization_id:
            organization_sql_where = f"AND core_organization.id={int(organization_id)}"

        study_sql_where = ""
        if study_id:
            study_sql_where = f"AND core_study.id={int(study_id)}"

        patient_id_sql_where = ""
        if patient_id:
            patient_id_sql_where = f"AND core_patient.id={int(patient_id)}"

        patient_identifier_value_sql_where = ""
        if patient_identifier_value:
            patient_identifier_value_sql_where = "AND core_patient.identifier=%(patient_identifier_value)s"

        query = f"""
            SELECT 1 AS id,
                   COUNT(DISTINCT core_patient.id) AS count
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
            WHERE core_practitionerorganization.practitioner_id = %(practitioner_id)s
              {organization_sql_where}
              {study_sql_where}
              {patient_id_sql_where}
              {patient_identifier_value_sql_where}
        """

        # Use a temporary model for count results
        class CountResult(models.Model):
            count = models.IntegerField()

            class Meta:
                managed = False
                app_label = "core"

        params = {"practitioner_id": practitioner_id}
        if patient_identifier_value:
            params["patient_identifier_value"] = patient_identifier_value
        results = list(CountResult.objects.raw(query, params))
        return results[0].count if results else 0

    @staticmethod
    def practitioner_authorized(
        jhe_user_id,
        patient_id=None,
        patient_identifier_system=None,
        patient_identifier_value=None,
        organization_id=None,
    ):
        if (
            len(
                Patient.for_practitioner_organization_study(
                    jhe_user_id,
                    organization_id,
                    None,
                    patient_id,
                    patient_identifier_value,
                    page=1,
                    pageSize=20,
                )
            )
            == 0
        ):
            return False
        return True

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
        offset=None,
        page=None,
    ):

        # Explicitly cast to ints so no injection vulnerability
        study_sql_where = ""
        if study_id:
            study_sql_where = "AND core_studypatient.study_id={study_id}".format(study_id=int(study_id))

        patient_identifier_value_sql_where = ""
        if patient_identifier_value:
            patient_identifier_value_sql_where = "AND core_patient.identifier=%(patient_identifier_value)s"

        # Set default values for pagination parameters
        offset = 0 if offset is None else int(offset)
        # FIXME: these are not actually used for pagination!
        # they set the unconditional boundaries on multi-page queries
        if page is not None:
            raise NotImplementedError("page not supported")
        # since this is unconditional, set it pretty high,
        # otherwise fetching more data than this is impossible
        limit = 10_000

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

            WHERE core_practitionerorganization.practitioner_id = (
              SELECT id
              FROM core_practitioner
              WHERE jhe_user_id = %(jhe_user_id)s
            )
            {study_sql_where}
            {patient_identifier_value_sql_where}
            ORDER BY core_patient.name_family
            LIMIT {limit}
            OFFSET {offset};
            """.format(
            SITE_URL=settings.SITE_URL,
            study_sql_where=study_sql_where,
            patient_identifier_value_sql_where=patient_identifier_value_sql_where,
            limit=limit,
            offset=offset,
        )

        records = Patient.objects.raw(
            q,
            {
                "jhe_user_id": jhe_user_id,
                "patient_identifier_value": patient_identifier_value,
            },
        )

        for record in records:
            # jsonb in raw is not automagically cast
            record.meta = json.loads(record.meta)
            record.identifier = json.loads(record.identifier)
            if len(record.identifier) == 0:
                del record.identifier
            record.name = json.loads(record.name)
            record.telecom = json.loads(record.telecom)
            # must delay importing to avoid circular import
            from core.serializers import FHIRPatientSerializer

            serializer = FHIRPatientSerializer(record)
            try:
                FHIRPatient.parse_obj(humps.camelize(serializer.data))
            except Exception as e:
                raise (BadRequest(e))  # TBD: move to view

        return records

    def __init__(self, *args, **kwargs):
        # Remove organization_id if it's passed in, as it should be handled by the M2M relationship
        self._organization_id = None
        if "organization_id" in kwargs:
            self._organization_id = kwargs.pop("organization_id")
        super().__init__(*args, **kwargs)
        self.telecom_email = None

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if hasattr(self, "_organization_id") and self._organization_id:
            try:
                PatientOrganization.objects.get_or_create(patient=self, organization_id=self._organization_id)
            except IntegrityError as e:
                print(f"IntegrityError: {e}")


"""
    Allows for a many-to-many relationship between organizations and practitioner users
"""


class PractitionerOrganization(models.Model):
    ROLE_MEMBER = "member"
    ROLE_MANAGER = "manager"
    ROLE_VIEWER = "viewer"

    ROLE_CHOICES = [
        (ROLE_MEMBER, "Member"),
        (ROLE_MANAGER, "Manager"),
        (ROLE_VIEWER, "Viewer"),
    ]

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="organization_links")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="practitioner_links")

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)

    class Meta:
        unique_together = ("practitioner", "organization")


"""
    Allows for a many-to-many relationship between organizations and patient users
"""


class PatientOrganization(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="organization_links")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="patient_links")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["patient_id", "organization_id"],
                name="core_patientorganization_unique_patient_id_organization_id",
            )
        ]


class CodeableConcept(models.Model):
    coding_system = models.CharField()
    coding_code = models.CharField()
    text = models.CharField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["coding_system", "coding_code"],
                name="core_codeableconcept_coding_system_coding_code",
            )
        ]


class Study(models.Model):
    name = models.CharField()
    description = models.TextField()
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    patients = models.ManyToManyField("Patient", through="StudyPatient")
    icon_url = models.TextField(null=True, blank=True)

    @staticmethod
    def for_practitioner_organization(jhe_user_id, organization_id=None, study_id=None, page=1, pageSize=20):
        practitioner = get_object_or_404(Practitioner, jhe_user_id=jhe_user_id)
        practitioner_id = practitioner.id

        page = int(page)
        pageSize = int(pageSize)
        offset = pageSize * (page - 1)
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
            LIMIT {pageSize}
            OFFSET {offset};
        """
        return Study.objects.raw(sql, {"practitioner_id": practitioner_id})

    @staticmethod
    def count_for_practitioner_organization(jhe_user_id, organization_id=None, study_id=None):
        """
        Use a dedicated count query for better performance. Returns the total number of studies matching the
        criteria.
        """
        practitioner = get_object_or_404(Practitioner, jhe_user_id=jhe_user_id)
        practitioner_id = practitioner.id
        study_sql_where = f"AND core_study.id = {int(study_id)}" if study_id else ""
        organization_sql_where = f"AND core_organization.id = {int(organization_id)}" if organization_id else ""

        class CountResult(models.Model):
            count = models.IntegerField()

            class Meta:
                managed = False
                app_label = "core"

        query = f"""
          SELECT 1 AS id, COUNT(DISTINCT core_study.id) AS count
          FROM core_study
          JOIN core_organization
            ON core_organization.id = core_study.organization_id
          JOIN core_practitionerorganization
            ON core_practitionerorganization.organization_id = core_organization.id
          WHERE core_practitionerorganization.practitioner_id = %(practitioner_id)s
          {study_sql_where}
          {organization_sql_where}
        """
        results = list(CountResult.objects.raw(query, {"practitioner_id": practitioner_id}))
        return results[0].count if results else 0

    @staticmethod
    def practitioner_authorized(practitioner_user_id, study_id):
        if len(Study.for_practitioner_organization(practitioner_user_id, None, study_id)) == 0:
            return False
        return True

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
        q = """
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
            """.format(
            sql_scope_code=sql_scope_code
        )

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
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)

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
    scope_code = models.ForeignKey(CodeableConcept, on_delete=models.CASCADE)
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
    scope_code = models.ForeignKey(CodeableConcept, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["study", "scope_code"],
                name="core_studyscoperequest_unique_study_id_scope_code_id",
            )
        ]


class DataSource(models.Model):
    DATA_SOURCE_TYPE_CHOICES = {"personal_device": "Personal Device"}
    name = models.CharField(null=True, blank=False)
    type = models.CharField(
        choices=DATA_SOURCE_TYPE_CHOICES,
        null=False,
        blank=False,
        default="personal_device",
    )

    # this will never be large
    @staticmethod
    def data_sources_with_scopes(data_source_id=None, study_id=None):

        # Explicitly cast to ints so no injection vulnerability
        sql_where = ""
        sql_join = ""
        if data_source_id:
            sql_where = "WHERE core_datasource.id={data_source_id}".format(data_source_id=int(data_source_id))
        elif study_id:
            sql_join = "JOIN core_studydatasource ON core_studydatasource.data_source_id=core_datasource.id"
            sql_where = "WHERE core_studydatasource.study_id={study_id}".format(study_id=int(study_id))

        q = """
            SELECT core_datasource.*
            FROM core_datasource
            {sql_join}
            {sql_where}
            ORDER BY core_datasource.name
            """.format(
            sql_join=sql_join, sql_where=sql_where
        )

        data_sources = DataSource.objects.raw(q)

        q = """
            SELECT core_codeableconcept.*
            FROM core_codeableconcept
            JOIN core_datasourcesupportedscope ON core_datasourcesupportedscope.scope_code_id=core_codeableconcept.id
            WHERE core_datasourcesupportedscope.data_source_id=%(data_source_id)s
            ORDER BY text
            """

        for data_source in data_sources:
            for scope in CodeableConcept.objects.raw(q, {"data_source_id": data_source.id}):
                data_source.supported_scopes.append(scope)

        return data_sources

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.supported_scopes = []


class DataSourceSupportedScope(models.Model):
    data_source = models.ForeignKey(DataSource, on_delete=models.CASCADE)
    scope_code = models.ForeignKey(CodeableConcept, on_delete=models.CASCADE)


class StudyDataSource(models.Model):
    study = models.ForeignKey(Study, on_delete=models.CASCADE)
    data_source = models.ForeignKey(DataSource, on_delete=models.CASCADE)


# Observation per record: https://stackoverflow.com/a/61484800 (author worked at ONC)
class Observation(models.Model):
    subject_patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    codeable_concept = models.ForeignKey(CodeableConcept, on_delete=models.CASCADE)
    data_source = models.ForeignKey(DataSource, on_delete=models.SET_NULL, null=True)
    value_attachment_data = models.JSONField()
    last_updated = models.DateTimeField(auto_now=True)

    # https://build.fhir.org/valueset-observation-status.html
    OBSERVATION_STATUS_CHOICES = {
        "registered": "registered",
        "preliminary": "preliminary",
        "final": "final",
        "amended": "amended",
        "corrected": "corrected",
        "appended": "appended",
        "cancelled": "cancelled",
        "entered-in-error": "Entered in Error",
        "unknown": "Unknown",
    }

    status = models.CharField(choices=OBSERVATION_STATUS_CHOICES, null=False, blank=False, default="final")

    @staticmethod
    def for_practitioner_organization_study_patient(
        jhe_user_id,
        organization_id=None,
        study_id=None,
        patient_id=None,
        observation_id=None,
        page=1,
        pageSize=20,
    ):

        if isinstance(pageSize, str):
            pageSize = int(pageSize)

        if isinstance(page, str):
            page = int(page)

        print(f"pageSize: {pageSize}, page: {page}")

        offset = pageSize * (page - 1)

        # Explicitly cast to ints so no injection vulnerability
        organization_sql_where = ""
        if organization_id:
            organization_sql_where = "AND core_organization.id={organization_id}".format(
                organization_id=int(organization_id)
            )

        study_sql_where = ""
        if study_id:
            study_sql_where = "AND core_study.id={study_id}".format(study_id=int(study_id))

        patient_id_sql_where = ""
        if patient_id:
            patient_id_sql_where = "AND core_patient.id={patient_id}".format(patient_id=int(patient_id))

        observation_sql_where = ""
        if observation_id:
            observation_sql_where = "AND core_observation.id={observation_id}".format(
                observation_id=int(observation_id)
            )

        # noqa
        q = """
        SELECT DISTINCT(core_observation.*),
        core_observation.value_attachment_data as value_attachment_data_json,
        core_codeableconcept.coding_system as coding_system,
        core_codeableconcept.coding_code as coding_code,
        core_codeableconcept.text as coding_text,
        core_patient.name_family as patient_name_family,
        core_patient.name_given as patient_name_given

        FROM core_observation
        JOIN core_codeableconcept ON core_codeableconcept.id=core_observation.codeable_concept_id
        JOIN core_patient ON core_patient.id=core_observation.subject_patient_id
        JOIN core_patientorganization ON core_patientorganization.patient_id=core_patient.id
        JOIN core_organization ON core_organization.id=core_patientorganization.organization_id
        JOIN core_practitionerorganization ON core_practitionerorganization.organization_id=core_organization.id
        LEFT JOIN core_studypatient ON core_studypatient.patient_id=core_patient.id
        LEFT JOIN core_study ON core_study.id=core_studypatient.study_id
        WHERE core_practitionerorganization.practitioner_id=%(jhe_user_id)s
        {organization_sql_where}
        {study_sql_where}
        {patient_id_sql_where}
        {observation_sql_where}
        ORDER BY core_observation.last_updated DESC
        LIMIT %(pageSize)s OFFSET %(offset)s;
        """.format(
            organization_sql_where=organization_sql_where,
            study_sql_where=study_sql_where,
            patient_id_sql_where=patient_id_sql_where,
            observation_sql_where=observation_sql_where,
        )

        practitioner = Practitioner.objects.get(jhe_user_id=jhe_user_id)
        practitioner_id = practitioner.id

        print(f"practitioner_id: {practitioner_id}")

        return Observation.objects.raw(q, {"jhe_user_id": practitioner_id, "pageSize": pageSize, "offset": offset})

    @staticmethod
    def practitioner_authorized(practitioner_user_id, observation_id):
        if (
            len(
                Observation.for_practitioner_organization_study_patient(
                    practitioner_user_id, None, None, None, observation_id
                )
            )
            == 0
        ):
            return False
        return True

    @staticmethod
    def fhir_search(
        jhe_user_id,
        study_id=None,
        patient_id=None,
        patient_identifier_system=None,
        patient_identifier_value=None,
        coding_system=None,
        coding_code=None,
        observation_id=None,
        offset=None,
        page=None,
    ):

        # Explicitly cast to ints so no injection vulnerability
        study_sql_where = ""
        if study_id:
            study_sql_where = "AND core_study.id={study_id}".format(study_id=int(study_id))

        patient_id_sql_where = ""
        if patient_id:
            patient_id_sql_where = "AND core_patient.id={patient_id}".format(patient_id=int(patient_id))

        patient_identifier_value_sql_where = ""
        if patient_identifier_value:
            patient_identifier_value_sql_where = "AND core_patient.identifier=%(patient_identifier_value)s"

        observation_sql_where = ""
        if observation_id:
            observation_sql_where = "AND core_observation.id={observation_id}".format(
                observation_id=int(observation_id)
            )

        # Set default values for pagination parameters
        offset = 0 if offset is None else int(offset)
        # FIXME: these are not actually used for pagination!
        # they set the unconditional boundaries on multi-page queries
        if page is not None:
            raise NotImplementedError("page not supported")
        # since this is unconditional, set it pretty high,
        # otherwise fetching more data than this is impossible
        limit = 10_000

        print(f"jhe_user_id: {jhe_user_id}")
        if not patient_id:
            patient = get_object_or_404(Patient, jhe_user_id=jhe_user_id)

            print(f"patient: {patient}")

            patient_user_id = patient.id
        else:
            patient_user_id = patient_id

        print(f"patient_user_id: {patient_user_id}")

        # TBD: Query optimization: https://stackoverflow.com/a/6037376
        # pagination: https://github.com/mattbuck85/django-paginator-rawqueryset
        q = """
            SELECT  'Observation' as resource_type,
                    'final' as status,
                    core_observation.id as id,
                    core_observation.id::varchar as id_string,
                    -- ('{SITE_URL}/fhir/r5/Observation/' || core_observation.id) as full_url,

                    json_build_object(
                        'last_updated',
                        core_observation.last_updated
                    )::jsonb as meta,

                                                                      -- double bracket for python .format ignore
                    jsonb_agg(to_jsonb(core_observationidentifier) - '{{id, observation_id}}'::text[]) as identifier,

                    json_build_object(
                        'reference',
                        'Patient/' || core_observation.subject_patient_id
                    )::jsonb as subject,

                    json_build_object(
                        'coding',
                        json_build_array(
                            json_build_object(
                                'system', core_codeableconcept.coding_system,
                                'code', core_codeableconcept.coding_code
                            )
                        )
                    )::jsonb as code,

                    json_build_object(
                        'content_type',
                        'application/json',
                        'data',
                        encode(convert_to(core_observation.value_attachment_data::text, 'UTF-8'), 'base64')
                    )::jsonb as value_attachment

            FROM core_observation
            LEFT JOIN core_observationidentifier ON core_observationidentifier.observation_id=core_observation.id
            JOIN core_codeableconcept ON core_codeableconcept.id=core_observation.codeable_concept_id
            JOIN core_patient ON core_patient.id=core_observation.subject_patient_id
            LEFT JOIN core_studypatient ON core_studypatient.patient_id=core_patient.id
            LEFT JOIN core_study ON core_study.id=core_studypatient.study_id
            JOIN core_organization ON core_organization.id=core_study.organization_id
            JOIN core_patientorganization ON core_patientorganization.organization_id=core_organization.id
            WHERE core_patientorganization.patient_id={patient_user_id}
AND core_codeableconcept.coding_system LIKE %(coding_system)s AND core_codeableconcept.coding_code LIKE %(coding_code)s
            {study_sql_where}
            {patient_id_sql_where}
            {patient_identifier_value_sql_where}
            {observation_sql_where}
            GROUP BY core_observation.id, core_codeableconcept.coding_system, core_codeableconcept.coding_code
            ORDER BY core_observation.last_updated DESC
            LIMIT {limit}
            OFFSET {offset};
            """.format(
            SITE_URL=settings.SITE_URL,
            patient_user_id=patient_user_id,
            study_sql_where=study_sql_where,
            patient_id_sql_where=patient_id_sql_where,
            patient_identifier_value_sql_where=patient_identifier_value_sql_where,
            observation_sql_where=observation_sql_where,
            limit=limit,
            offset=offset,
        )

        records = Observation.objects.raw(
            q,
            {
                "coding_system": coding_system if coding_system else "%",
                "coding_code": coding_code if coding_code else "%",
                "patient_identifier_value": patient_identifier_value,
            },
        )

        for record in records:
            # jsonb in raw is not automagically cast
            record.meta = json.loads(record.meta)
            # Extra handling if list can potentially contain nulls
            record.identifier = list(filter(lambda item: item is not None, json.loads(record.identifier)))
            if len(record.identifier) == 0:
                del record.identifier
            record.subject = json.loads(record.subject)
            record.code = json.loads(record.code)
            record.value_attachment = json.loads(record.value_attachment)
            # have to delay this import to avoid circular import
            from core.serializers import FHIRObservationSerializer

            serializer = FHIRObservationSerializer(record)
            try:
                FHIRObservation.parse_obj(humps.camelize(serializer.data))
            except Exception as e:
                raise (BadRequest(e))  # TBD: move to view

        return records

    @staticmethod
    def count_for_practitioner_organization_study_patient(
        jhe_user_id,
        organization_id=None,
        study_id=None,
        patient_id=None,
        observation_id=None,
    ):
        """Use a dedicated count query for better performance. Aligns with optional inclusion for _total number of
        matching resources per FHIR spec."""

        # Explicitly cast to ints so no injection vulnerability
        organization_sql_where = ""
        if organization_id:
            organization_sql_where = f"AND core_organization.id={int(organization_id)}"

        study_sql_where = ""
        if study_id:
            study_sql_where = f"AND core_study.id={int(study_id)}"

        patient_id_sql_where = ""
        if patient_id:
            patient_id_sql_where = f"AND core_patient.id={int(patient_id)}"

        observation_sql_where = ""
        if observation_id:
            observation_sql_where = f"AND core_observation.id={int(observation_id)}"

        practitioner = Practitioner.objects.get(jhe_user_id=jhe_user_id)
        practitioner_id = practitioner.id

        # Use a temporary model for count results
        class CountResult(models.Model):
            count = models.IntegerField()

            class Meta:
                managed = False  # No table creation
                app_label = "core"

        query = f"""
        SELECT 1 as id, COUNT(DISTINCT core_observation.id) as count
        FROM core_observation
        JOIN core_codeableconcept ON core_codeableconcept.id=core_observation.codeable_concept_id
        JOIN core_patient ON core_patient.id=core_observation.subject_patient_id
        JOIN core_patientorganization ON core_patientorganization.patient_id=core_patient.id
        JOIN core_organization ON core_organization.id=core_patientorganization.organization_id
        JOIN core_practitionerorganization ON core_practitionerorganization.organization_id=core_organization.id
        LEFT JOIN core_studypatient ON core_studypatient.patient_id=core_patient.id
        LEFT JOIN core_study ON core_study.id=core_studypatient.study_id
        WHERE core_practitionerorganization.practitioner_id=%(practitioner_id)s
        {organization_sql_where}
        {study_sql_where}
        {patient_id_sql_where}
        {observation_sql_where}
      """

        results = list(CountResult.objects.raw(query, {"practitioner_id": practitioner_id}))
        return results[0].count if results else 0

    # Get the binary data eg https://www.rapidtables.com/convert/number/string-to-binary.html (delimiter=none)
    # base64 it eg https://cryptii.com/pipes/binary-to-base64
    @staticmethod
    def fhir_create(data, user):

        # Validate Structure
        fhir_observation = None
        try:
            fhir_observation = FHIRObservation.parse_obj(humps.camelize(data))
        except Exception as e:
            raise (BadRequest(e))  # TBD: move to view

        # Check Patient
        subject_patient = None
        if (
            not fhir_observation.subject
            or not fhir_observation.subject.reference
            or not fhir_observation.subject.reference.startswith("Patient/")
        ):
            raise (
                BadRequest("Subject is required and must be a reference to a Patient ID and start with 'Patient/'")
            )  # TBD: move to view
        subject_patient_id = fhir_observation.subject.reference.split("/")[1]
        try:
            subject_patient = Patient.objects.get(pk=subject_patient_id)
        except Patient.DoesNotExist:
            raise (
                BadRequest(
                    "Patient id={subject_patient_id} can not be found.".format(subject_patient_id=subject_patient_id)
                )
            )  # TBD: move to view

        if user.is_practitioner():
            if not subject_patient.practitioner_authorized(user.pk, subject_patient.id):
                raise PermissionDenied("Current user doesn't have access to the Patient.")
            user_patient = subject_patient
        else:
            user_patient = user.get_patient()
        if user_patient is None:
            raise PermissionDenied("Current user is not a Patient.")

        if user_patient and (subject_patient.id != user_patient.id):
            raise PermissionDenied("The Subject Patient does not match the current user.")

        # Check Identifiers
        if fhir_observation.identifier:
            for identifier in fhir_observation.identifier:
                existing_ids = ObservationIdentifier.objects.filter(system=identifier.system, value=identifier.value)
                if len(existing_ids) > 0:
                    raise IntegrityError(
                        "Identifier already exists: system={system} value={value}".format(
                            system=identifier.system, value=identifier.value
                        )
                    )

        # Check Device
        data_source = None
        if (
            not fhir_observation.device
            or not fhir_observation.device.reference
            or not fhir_observation.device.reference.startswith("Device/")
        ):
            raise (
                BadRequest("Device is required and must be a reference to a Data Source ID and start with 'Device/'")
            )  # TBD: move to view
        device_id = fhir_observation.device.reference.split("/")[1]
        try:
            data_source = DataSource.objects.get((Q(type="personal_device") | Q(type="device")), id=device_id)
        except DataSource.DoesNotExist:
            raise (
                BadRequest("Device Data Source id={device_id} can not be found.".format(device_id=device_id))
            )  # TBD: move to view

        # Check Scope
        if len(fhir_observation.code.coding) == 0 or len(fhir_observation.code.coding) > 1:
            raise BadRequest("Exactly one Code must be provided.")  # TBD: move to view

        codeable_concepts = CodeableConcept.objects.filter(
            coding_system=fhir_observation.code.coding[0].system,
            coding_code=fhir_observation.code.coding[0].code,
        )

        if len(codeable_concepts) == 0:
            raise BadRequest(
                "Code not found: system={system} code={code}".format(
                    system=fhir_observation.code.coding[0].system,
                    code=fhir_observation.code.coding[0].code,
                )  # TBD: move to view
            )

        if codeable_concepts[0].id not in [scope.id for scope in user_patient.consolidated_consented_scopes()]:
            raise PermissionDenied(
                "Observation data with coding_system={coding_system} coding_code={coding_code} has not been consented"
                " for any studies by this Patient.".format(
                    coding_system=codeable_concepts[0].coding_system,
                    coding_code=codeable_concepts[0].coding_code,
                )
            )

        try:
            value_attachment_data_binary = base64.b64decode(fhir_observation.valueAttachment.data)
            value_attachment_data_json = value_attachment_data_binary.decode("ascii")
            value_attachment_data = json.loads(value_attachment_data_json)
        except Exception:
            raise BadRequest("valueAttachment.data must be Base 64 Encoded Binary JSON.")  # TBD: move to view

        observation = Observation.objects.create(
            subject_patient=subject_patient,
            data_source=data_source,
            codeable_concept=codeable_concepts[0],
            status=fhir_observation.status,
            value_attachment_data=value_attachment_data,
            last_updated=timezone.now,
        )

        if fhir_observation.identifier:
            for identifier in fhir_observation.identifier:
                ObservationIdentifier.objects.create(
                    observation=observation,
                    system=identifier.system,
                    value=identifier.value,
                )

        return observation

    @staticmethod
    def validate_outer_schema(instance_data):
        for name in ("data-point-1.0.json", "data-series-1.0.json"):
            schema = json.loads((settings.DATA_DIR_PATH.schemas_metadata / name).read_text())
            try:
                validate_with_registry(instance=instance_data, schema=schema)
                return True
            except ValidationError:
                # Not a match; try the next outer schema
                continue
        # Neither matched as a valid outer schema
        return False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # FHIR serialization support
        self.identifier = None
        self.resource_type = None
        self.meta = None
        self.value_attachment = None
        self.subject = None
        self.code = None

    def clean(self):
        try:
            value_attachment_data = self.value_attachment_data

            header_schema = json.loads((settings.DATA_DIR_PATH.schemas_metadata / "header-1.0.json").read_text())
            validate_with_registry(instance=value_attachment_data.get("header"), schema=header_schema)

            body_schema = json.loads(
                (
                    settings.DATA_DIR_PATH.schemas_data
                    / f"schema-{self.codeable_concept.coding_code.replace(':', '_').replace('.', '-')}.json"
                ).read_text()
            )
            validate_with_registry(instance=value_attachment_data.get("body"), schema=body_schema)
        except Exception as error:
            raise error

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class ObservationIdentifier(models.Model):
    observation = models.ForeignKey(Observation, on_delete=models.CASCADE)
    system = models.CharField(null=True, blank=False)
    value = models.CharField(null=True, blank=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["system", "value"],
                name="core_observation_identifier_unique_observation_system_value",
            )
        ]
