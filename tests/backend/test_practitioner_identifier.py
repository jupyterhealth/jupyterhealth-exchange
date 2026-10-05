import pytest
from django.db.utils import IntegrityError

from core.models import PractitionerIdentifier

EHR = "https://ehr.example.org"


def test_system_and_value_pair_is_unique(user):
    PractitionerIdentifier.objects.create(practitioner=user.practitioner, system=EHR, value="Practitioner-123")

    with pytest.raises(IntegrityError):
        PractitionerIdentifier.objects.create(practitioner=user.practitioner, system=EHR, value="Practitioner-123")


def test_same_value_under_another_system_is_allowed(user):
    PractitionerIdentifier.objects.create(practitioner=user.practitioner, system=EHR, value="Practitioner-123")
    PractitionerIdentifier.objects.create(
        practitioner=user.practitioner, system="https://other.example.org", value="Practitioner-123"
    )

    assert user.practitioner.identifiers.count() == 2


def test_deleting_the_user_deletes_its_identifiers(user):
    PractitionerIdentifier.objects.create(practitioner=user.practitioner, system=EHR, value="Practitioner-123")

    user.delete()

    assert not PractitionerIdentifier.objects.exists()
