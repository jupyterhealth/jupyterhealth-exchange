"""Deleting a Practitioner/Patient must not leave its JheUser behind.

JheUser.delete() raw-deletes the user row instead of letting Django's collector cascade
(the auth M2M tables were dropped), so it has to clean up every table that FKs to
core_jheuser itself. It used to miss account_emailaddress, django_admin_log and
oauth2_provider_devicegrant -- and because Django's FKs are DEFERRABLE INITIALLY DEFERRED
the raw DELETE reported success and only blew up at COMMIT, after the profile delete had
already committed. The result was an orphaned JheUser: no profile, not a superuser,
holding its email address hostage.
"""

from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import (
    AccessToken,
    get_application_model,
    get_device_grant_model,
)
from rest_framework.test import APIClient

from core.models import JheUser, Patient, PatientOrganization, Practitioner

Application = get_application_model()
DeviceGrant = get_device_grant_model()


def _superuser_client(superuser):
    api_client = APIClient()
    api_client.default_format = "json"
    api_client.force_authenticate(superuser)
    return api_client


def _make_practitioner(email="orphan-prac@example.org"):
    user = JheUser.objects.create_user(email=email, password="testpass123", identifier=email, user_type="practitioner")
    return user, user.practitioner


def _assert_no_dangling_references():
    """Force the deferred FK checks Postgres would otherwise only run at COMMIT.

    Without this the delete looks fine from inside the test transaction and the breakage
    only shows up as a teardown error -- which is exactly how the bug went unnoticed in
    production, where the failure landed on the caller's commit.
    """
    connection.check_constraints()


def _assert_gone(user_id, practitioner_id):
    _assert_no_dangling_references()
    assert not Practitioner.objects.filter(id=practitioner_id).exists()
    assert not JheUser.objects.filter(id=user_id).exists()


# --- the dependent rows that used to break the delete -------------------------------------


def test_delete_practitioner_with_allauth_email_address(superuser):
    """A practitioner who has signed in through allauth has an account_emailaddress row."""
    user, practitioner = _make_practitioner()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

    r = _superuser_client(superuser).delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code == 204, r.text
    _assert_gone(user.id, practitioner.id)
    assert not EmailAddress.objects.filter(email=user.email).exists()


def test_delete_practitioner_with_admin_log_entries(superuser):
    """A practitioner who has used the Django admin has django_admin_log rows."""
    user, practitioner = _make_practitioner()
    LogEntry.objects.create(
        user_id=user.id,
        content_type=ContentType.objects.get_for_model(Practitioner),
        object_id=str(practitioner.id),
        object_repr=str(practitioner),
        action_flag=ADDITION,
        change_message="created",
    )

    r = _superuser_client(superuser).delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code == 204, r.text
    _assert_gone(user.id, practitioner.id)


def test_delete_practitioner_with_device_grant(superuser):
    user, practitioner = _make_practitioner()
    app = Application.objects.create(
        name="device app",
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    DeviceGrant.objects.create(
        user=user,
        device_code="dc-1",
        user_code="uc-1",
        client_id=app.client_id,
        expires=timezone.now() + timedelta(minutes=10),
    )

    r = _superuser_client(superuser).delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code == 204, r.text
    _assert_gone(user.id, practitioner.id)


def test_delete_patient_with_allauth_email_address(api_client, organization):
    """Same fix on the patient delete path (patients get an EmailAddress from OTP login)."""
    email = "orphan-patient@example.org"
    user = JheUser.objects.create_user(email=email, password="x", user_type="patient")
    patient = user.patient
    PatientOrganization.objects.create(patient=patient, organization=organization)
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)

    r = api_client.delete(f"/api/v1/patients/{patient.id}?organization_id={organization.id}")

    assert r.status_code == 204, r.text
    _assert_no_dangling_references()
    assert not Patient.objects.filter(id=patient.id).exists()
    assert not JheUser.objects.filter(id=user.id).exists()


# --- atomicity ----------------------------------------------------------------------------


def test_failed_user_delete_does_not_orphan_the_practitioner(superuser, monkeypatch):
    """If the user delete fails, the practitioner delete must roll back with it."""
    user, practitioner = _make_practitioner()

    def boom(self, *args, **kwargs):
        raise RuntimeError("simulated failure inside JheUser.delete()")

    monkeypatch.setattr(JheUser, "delete", boom)

    with pytest.raises(RuntimeError):
        _superuser_client(superuser).delete(f"/api/v1/practitioners/{practitioner.id}")

    # neither row may have gone away on its own
    assert Practitioner.objects.filter(id=practitioner.id).exists()
    assert JheUser.objects.filter(id=user.id).exists()


# --- the Application cascade --------------------------------------------------------------


def _make_app(owner, name):
    return Application.objects.create(
        name=name,
        user=owner,
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
    )


def test_deleting_user_keeps_application_other_users_still_use(superuser):
    """Deleting an Application cascades to every token issued for it, so a shared app is
    detached from its owner rather than deleted -- other users' tokens must survive."""
    user, practitioner = _make_practitioner()
    other = JheUser.objects.create_user(email="other@example.org", password="x", user_type="practitioner")
    app = _make_app(user, "shared app")
    other_token = AccessToken.objects.create(
        user=other, application=app, token="other-token", expires=timezone.now() + timedelta(hours=1)
    )
    own_token = AccessToken.objects.create(
        user=user, application=app, token="own-token", expires=timezone.now() + timedelta(hours=1)
    )

    r = _superuser_client(superuser).delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code == 204, r.text
    app.refresh_from_db()
    assert app.user_id is None, "shared application should be detached, not deleted"
    assert AccessToken.objects.filter(id=other_token.id).exists(), "other user's token was destroyed"
    assert not AccessToken.objects.filter(id=own_token.id).exists()


def test_deleting_user_deletes_its_own_application(superuser):
    """A practitioner's own client-credentials app (no other user's tokens) still goes."""
    user, practitioner = _make_practitioner()
    app = _make_app(user, "practitioner client")
    AccessToken.objects.create(
        user=None,  # client-credentials tokens have no user
        application=app,
        token="cc-token",
        expires=timezone.now() + timedelta(hours=1),
    )

    r = _superuser_client(superuser).delete(f"/api/v1/practitioners/{practitioner.id}")

    assert r.status_code == 204, r.text
    assert not Application.objects.filter(id=app.id).exists()


# --- the Django admin ---------------------------------------------------------------------


@pytest.fixture
def admin_browser(db, client):
    su = JheUser.objects.create_superuser(email="admin-ui@example.org", password="pw")
    client.force_login(su)
    return client


def test_admin_practitioner_delete_removes_user(admin_browser):
    user, practitioner = _make_practitioner("admin-deleted@example.org")
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    url = reverse("admin:core_practitioner_delete", args=[practitioner.id])

    confirm = admin_browser.get(url)
    assert confirm.status_code == 200
    assert "admin-deleted@example.org" in confirm.content.decode(), "confirmation page must name the user"

    r = admin_browser.post(url, {"post": "yes"})

    assert r.status_code in (200, 302)
    _assert_gone(user.id, practitioner.id)


def test_admin_practitioner_bulk_delete_removes_user(admin_browser):
    user, practitioner = _make_practitioner("admin-bulk@example.org")
    url = reverse("admin:core_practitioner_changelist")

    confirm = admin_browser.post(url, {"action": "delete_selected", "_selected_action": [str(practitioner.id)]})
    assert confirm.status_code == 200
    r = admin_browser.post(
        url,
        {"action": "delete_selected", "_selected_action": [str(practitioner.id)], "post": "yes"},
    )

    assert r.status_code in (200, 302)
    _assert_gone(user.id, practitioner.id)


def test_admin_patient_delete_removes_user(admin_browser):
    user = JheUser.objects.create_user(email="admin-patient@example.org", password="x", user_type="patient")
    patient = user.patient
    url = reverse("admin:core_patient_delete", args=[patient.id])

    assert admin_browser.get(url).status_code == 200
    r = admin_browser.post(url, {"post": "yes"})

    assert r.status_code in (200, 302)
    _assert_no_dangling_references()
    assert not Patient.objects.filter(id=patient.id).exists()
    assert not JheUser.objects.filter(id=user.id).exists()


def test_admin_practitioner_delete_keeps_superuser_login(admin_browser):
    su = JheUser.objects.create_superuser(email="su-prac@example.org", password="pw")
    practitioner = su.practitioner  # signals create one for every superuser
    url = reverse("admin:core_practitioner_delete", args=[practitioner.id])

    r = admin_browser.post(url, {"post": "yes"})

    assert r.status_code in (200, 302)
    assert not Practitioner.objects.filter(id=practitioner.id).exists()
    assert JheUser.objects.filter(id=su.id).exists()
