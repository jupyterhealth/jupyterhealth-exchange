import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils.crypto import get_random_string
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import (
    JheUser,
    Organization,
    Patient,
    Practitioner,
    PractitionerOrganization,
)
from core.permissions import IsSuperUser
from core.serializers import (
    PractitionerSerializer,
)

logger = logging.getLogger(__name__)


class PractitionerViewSet(ModelViewSet):
    serializer_class = PractitionerSerializer
    queryset = Practitioner.objects.all()
    permission_classes = [IsAuthenticated, IsSuperUser]

    def create(self, request, *args, **kwargs):
        # Build the JheUser + Practitioner + organization link by hand. The default
        # ModelViewSet create can't do this: PractitionerSerializer is read-only (the email
        # and organizations are computed fields), and a Practitioner needs a JheUser login.
        # Mirrors the patient create path (core/views/patient.py): validate up front so the
        # user gets a clear field-keyed 400 instead of a KeyError/IntegrityError 500.
        email = (request.data.get("telecom_email") or "").strip()
        if not email:
            raise ValidationError(["Email is required to create a practitioner."])
        try:
            validate_email(email)
        except DjangoValidationError:
            raise ValidationError(["Enter a valid email address."])

        organization = None
        organization_id = request.data.get("organization_id")
        if organization_id:
            organization = Organization.objects.filter(id=organization_id).first()
            if organization is None:
                raise ValidationError(["Organization not found."])

        # Wrap user + practitioner + organization link in one transaction so any failure
        # rolls back the whole create, leaving no orphan JheUser/Practitioner.
        with transaction.atomic():
            jhe_user = JheUser.objects.filter(email=email).first()
            if jhe_user is None:
                jhe_user = JheUser(email=email)
                jhe_user.set_password(get_random_string(length=16))
                jhe_user.save()
            practitioner, _ = Practitioner.objects.get_or_create(
                jhe_user=jhe_user,
                defaults={
                    "name_family": request.data.get("name_family"),
                    "name_given": request.data.get("name_given"),
                },
            )
            if organization is not None:
                PractitionerOrganization.objects.get_or_create(
                    practitioner=practitioner,
                    organization=organization,
                )

        serializer = PractitionerSerializer(practitioner)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        practitioner = self.get_object()
        user = practitioner.jhe_user
        practitioner.delete()

        # Remove the now-orphaned JheUser so the email is reusable. Mirrors the patient
        # delete path (core/views/patient.py). Keep the user if another profile still
        # references it, or if it is a superuser (avoid deleting admin logins).
        if user and not user.is_superuser and not Patient.objects.filter(jhe_user_id=user.id).exists():
            user.delete()

        return Response({"success": True})
