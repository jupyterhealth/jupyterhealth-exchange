import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from core.models import Practitioner
from core.permissions import IsSuperUser
from core.serializers import (
    PractitionerSerializer,
)

logger = logging.getLogger(__name__)


class PractitionerViewSet(ModelViewSet):
    serializer_class = PractitionerSerializer
    queryset = Practitioner.objects.all()
    permission_classes = [IsAuthenticated, IsSuperUser]