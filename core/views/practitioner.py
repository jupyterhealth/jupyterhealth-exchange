import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.models import Patient, Practitioner
from core.permissions import IsSuperUser
from core.serializers import (
    PractitionerSerializer,
)

logger = logging.getLogger(__name__)


class PractitionerViewSet(ModelViewSet):
    serializer_class = PractitionerSerializer
    queryset = Practitioner.objects.all()
    permission_classes = [IsAuthenticated, IsSuperUser]

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
