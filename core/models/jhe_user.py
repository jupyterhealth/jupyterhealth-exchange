import logging

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
from oauth2_provider.models import (
    AccessToken,
    Grant,
    IDToken,
    RefreshToken,
    get_application_model,
    get_device_grant_model,
)

from core.services.jhe_settings import get_setting

from .organization import Organization
from .patient import Patient
from .practitioner import Practitioner, PractitionerOrganization

logger = logging.getLogger(__name__)


class JheUserManager(BaseUserManager):
    def create_user(self, email, password=None, user_type=None, **extra_fields):
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
    user_type = models.TextField(choices=list(USER_TYPES.items()), null=True, blank=True)

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
        - Proactively deletes every row that FKs to core_jheuser: Django's cascade collector
          never runs here, so anything missed is left dangling.
        - Finally, raw-DELETE the user row.

        Django declares its foreign keys DEFERRABLE INITIALLY DEFERRED, so a dependent row we
        forget to clean up does *not* make the raw DELETE fail: it reports success and the
        violation only surfaces at COMMIT, in the caller's frame. That is how orphaned JheUser
        rows were being created -- the profile delete had already committed by then. Step 4
        forces the check inside this transaction so a missed table fails loudly instead.
        """
        # 1) Remove Django OAuth Toolkit artifacts referencing this user
        # (Order chosen to avoid FK surprises across Django OAuth Toolkit versions)
        IDToken.objects.filter(user=self).delete()
        Grant.objects.filter(user=self).delete()
        RefreshToken.objects.filter(user=self).delete()  # often FK→AccessToken and FK→User
        AccessToken.objects.filter(user=self).delete()
        get_device_grant_model().objects.filter(user=self).delete()

        # 2) Applications this user owns (e.g. a practitioner's client-credentials client).
        # Deleting an Application cascades to *every* token ever issued for it, including
        # other users' -- so only delete the ones nobody else is using and merely detach the
        # rest (Application.user is nullable), leaving them working for their other users.
        Application = get_application_model()
        owned_applications = Application.objects.filter(user=self)
        # This user's own tokens are already gone, so any token left with a user is someone else's.
        shared_application_ids = set()
        for token_model in (AccessToken, RefreshToken, Grant, IDToken):
            shared_application_ids.update(
                token_model.objects.filter(application__in=owned_applications, user__isnull=False).values_list(
                    "application_id", flat=True
                )
            )
        Application.objects.filter(id__in=shared_application_ids).update(user=None)
        owned_applications.exclude(id__in=shared_application_ids).delete()

        # 3) The remaining tables that FK to core_jheuser. Imported here rather than at module
        # scope: this module is loaded while the app registry is still populating.
        from allauth.account.models import EmailAddress  # noqa: PLC0415
        from django.contrib.admin.models import LogEntry  # noqa: PLC0415

        EmailAddress.objects.filter(user=self).delete()  # cascades to EmailConfirmation
        LogEntry.objects.filter(user=self).delete()

        # 4) Delete profile rows via ORM so Django cascades (PractitionerOrganization, etc.)
        Practitioner.objects.filter(jhe_user=self).delete()
        Patient.objects.filter(jhe_user=self).delete()

        # 5) Now delete the user row itself (bypasses Django's M2M cleanup), then force the
        # deferred FK checks so anything still referencing the user raises here -- rolling
        # back this atomic block -- instead of at the caller's COMMIT.
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM core_jheuser WHERE id = %s", [self.id])
            deleted = cursor.rowcount
            if connection.vendor == "postgresql":
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")

        if deleted:
            return deleted
        raise ObjectDoesNotExist(f"JheUser with id={self.id} did not exist")

    def delete_if_unused(self):
        """Delete this user if no Patient/Practitioner profile references it any more.

        Called after a profile is deleted so the email address becomes reusable again. The
        user is kept when another profile still points at it, and superusers are always kept
        (deleting a profile must never strand an admin login). Returns True if it was deleted.
        """
        if self.is_superuser:
            return False
        if Practitioner.objects.filter(jhe_user_id=self.id).exists():
            return False
        if Patient.objects.filter(jhe_user_id=self.id).exists():
            return False
        self.delete()
        return True

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
        # Deferred: core.auth imports DOT's oauth2_validators, which calls
        # get_application_model() at module level. Importing it here at
        # model-definition time would run before the app registry is ready.
        from core.auth import account_activation_token

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Don't initialize patient here since it's a property without a setter
