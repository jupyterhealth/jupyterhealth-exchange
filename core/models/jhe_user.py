import base64
import hashlib
import json
import logging
from datetime import timedelta
from random import SystemRandom

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import BadRequest, ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMessage
from django.db import connection, models, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext_lazy as _
from oauth2_provider.models import AccessToken, Grant, IDToken, RefreshToken, get_application_model, get_grant_model

from core.jhe_settings.service import get_setting
from core.tokens import account_activation_token

from .organization import Organization
from .patient import Patient
from .practitioner import Practitioner, PractitionerOrganization

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
    USER_TYPES = {
        "patient": "Patient",
        "practitioner": "Practitioner",
    }
    user_type = models.CharField(max_length=12, choices=list(USER_TYPES.items()), null=True, blank=True)

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

        # 2) Delete profile rows via ORM so Django cascades (PractitionerOrganization, etc.)
        Practitioner.objects.filter(jhe_user=self).delete()
        Patient.objects.filter(jhe_user=self).delete()

        # 3) Now delete the user row itself (bypasses Django's M2M cleanup)
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
                Patient.objects.create(
                    jhe_user=self,
                    name_family=self.last_name or "",
                    name_given=self.first_name or "",
                    birth_date=timezone.now().date(),  # TBD, do we want a default value equivalent to this?
                    identifier=self.identifier,
                )
            elif self.user_type == "practitioner" and not hasattr(self, "practitioner_profile"):
                with transaction.atomic():
                    practitioner = Practitioner.objects.create(
                        jhe_user=self,
                        name_family=self.last_name,
                        name_given=self.first_name,
                        identifier=self.identifier,
                    )

                    # --- parse multi-org:role string from db ---
                    mapping_str = get_setting("auth.default_orgs", "")
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
                "site_url": get_setting("site.url", settings.SITE_URL),
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
    def create_authorization_code(self, application_id, code_verifier):
        self.last_login = timezone.now()
        self.save()

        Grant = get_grant_model()

        Grant.objects.filter(user_id=self.id, application_id=application_id).delete()

        # https://github.com/oauthlib/oauthlib/blob/f9a07c6c07d0ddac255dd322ef5fc54a7a46366d/oauthlib/common.py#L188
        UNICODE_ASCII_CHARACTER_SET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        authorization_code = "".join(SystemRandom().choice(UNICODE_ASCII_CHARACTER_SET) for _ in range(30))

        return Grant.objects.create(
            application_id=application_id,
            user_id=self.id,
            code=authorization_code,
            expires=timezone.now() + timedelta(seconds=settings.PATIENT_AUTHORIZATION_CODE_EXPIRE_SECONDS),
            redirect_uri=get_setting("site.url", settings.SITE_URL) + settings.OAUTH2_CALLBACK_PATH,
            scope="openid email",
            # https://github.com/oauthlib/oauthlib/blob/f9a07c6c07d0ddac255dd322ef5fc54a7a46366d/oauthlib/oauth2/rfc6749/grant_types/authorization_code.py#L18
            code_challenge=base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode(),
            code_challenge_method="S256",
            nonce="",
            claims=json.dumps({}),
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Don't initialize patient here since it's a property without a setter
