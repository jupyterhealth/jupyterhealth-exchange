"""A redeemed invitation link can be opened again for a limited time
(`auth.patient.invitation_redemption_window_hours`, 12 by default). That window runs from the
first use: opening the link again inside it must not push its end back."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from oauth2_provider.models import get_application_model

from core.models.patient_invitation import InvitationConflict, PatientInvitation

Application = get_application_model()


@pytest.fixture
def application(db):
    return Application.objects.create(
        name="Patient client",
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )


def _at(moment):
    """Pin the clock, for the model's own reads and for `auto_now` alike."""
    return patch("django.utils.timezone.now", return_value=moment)


def test_reuse_inside_the_window_is_allowed(patient, application):
    first_use = timezone.now()
    with _at(first_use):
        token = PatientInvitation.issue(patient, application).token
        PatientInvitation.redeem(token)

    with _at(first_use + timedelta(hours=11)):
        invitation, grant = PatientInvitation.redeem(token)

    assert invitation.status == PatientInvitation.Status.REDEEMED
    assert grant.code


def test_reuse_does_not_extend_the_window(patient, application):
    first_use = timezone.now()
    with _at(first_use):
        token = PatientInvitation.issue(patient, application).token
        PatientInvitation.redeem(token)

    with _at(first_use + timedelta(hours=11)):
        PatientInvitation.redeem(token)

    # 13 hours after the first use, but only 2 after the last one.
    with _at(first_use + timedelta(hours=13)), pytest.raises(InvitationConflict):
        PatientInvitation.redeem(token)


def test_link_is_refused_once_the_window_has_passed(patient, application):
    first_use = timezone.now()
    with _at(first_use):
        token = PatientInvitation.issue(patient, application).token
        PatientInvitation.redeem(token)

    with _at(first_use + timedelta(hours=13)), pytest.raises(InvitationConflict):
        PatientInvitation.redeem(token)
