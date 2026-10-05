"""Tests for the per-client email one-time-code login.

OAuth clients whose client_id is listed in the `auth.patient_access_clients`
JheSetting are routed from the password form (/accounts/login/) to the allauth
email-code flow (/accounts/login-otp/), preserving `next` so the OAuth authorize
flow resumes after login. All other clients keep the password form.
"""

import re

import pytest
from django.core import mail
from django.core.cache import cache
from django.urls import reverse

from core.models import JheSetting, JheUser

PATIENT_CLIENT_ID = "patient-access-cli"
OTHER_CLIENT_ID = "practitioner-cli"

# A representative authorize URL DOT would stuff into ?next= when bouncing an
# unauthenticated user to LOGIN_URL.
AUTHORIZE_NEXT = (
    "/o/authorize/?client_id={client_id}&response_type=code"
    "&redirect_uri=https://app.example.org/callback&scope=openid+email"
)


def set_patient_access_clients(value):
    setting, _ = JheSetting.objects.update_or_create(
        key="auth.patient_access_clients",
        defaults={"value_type": "json"},
    )
    setting.set_value("json", value)
    setting.save()
    # get_setting() caches for 60s; clear so the new value is read this test.
    cache.clear()


@pytest.fixture
def patient_user(db):
    return JheUser.objects.create_user(
        email="otp-patient@example.org",
        password="testpass123",
        user_type="patient",
    )


@pytest.fixture(autouse=True)
def clear_setting_cache():
    cache.clear()
    yield
    cache.clear()


def test_login_redirects_patient_access_client_to_otp(client, db):
    set_patient_access_clients([PATIENT_CLIENT_ID])
    response = client.get(
        reverse("login"),
        {"next": AUTHORIZE_NEXT.format(client_id=PATIENT_CLIENT_ID)},
    )
    assert response.status_code == 302
    assert response.url.startswith(reverse("login-otp"))
    # The original authorize URL is carried through for OAuth to resume.
    assert "client_id" in response.url and PATIENT_CLIENT_ID in response.url


def test_login_keeps_password_form_for_unlisted_client(client, db):
    set_patient_access_clients([PATIENT_CLIENT_ID])
    response = client.get(
        reverse("login"),
        {"next": AUTHORIZE_NEXT.format(client_id=OTHER_CLIENT_ID)},
    )
    assert response.status_code == 200


def test_login_keeps_password_form_without_next(client, db):
    set_patient_access_clients([PATIENT_CLIENT_ID])
    response = client.get(reverse("login"))
    assert response.status_code == 200


def test_login_no_redirect_when_next_has_no_client_id(client, db):
    set_patient_access_clients([PATIENT_CLIENT_ID])
    response = client.get(reverse("login"), {"next": "/clients/jhe-admin/"})
    assert response.status_code == 200


def test_authenticated_user_not_redirected_to_otp(client, patient_user):
    set_patient_access_clients([PATIENT_CLIENT_ID])
    client.force_login(patient_user)
    response = client.get(
        reverse("login"),
        {"next": AUTHORIZE_NEXT.format(client_id=PATIENT_CLIENT_ID)},
    )
    # Already authenticated: Django's LoginView bounces to `next`, never to the
    # OTP entry.
    assert not (response.status_code == 302 and response.url.startswith(reverse("login-otp")))


def test_otp_flow_logs_in_existing_patient(client, patient_user):
    set_patient_access_clients([PATIENT_CLIENT_ID])
    next_url = AUTHORIZE_NEXT.format(client_id=PATIENT_CLIENT_ID)

    # 1. Password form bounces the patient-access client to the OTP entry.
    login = client.get(reverse("login"), {"next": next_url})
    assert login.status_code == 302
    otp_url = login.url
    assert otp_url.startswith(reverse("login-otp"))

    # 2. Submitting the email mails a code and advances to the confirm step.
    request_code = client.post(otp_url, {"email": patient_user.email})
    assert request_code.status_code == 302
    assert request_code.url.startswith(reverse("account_confirm_login_code"))
    assert len(mail.outbox) == 1
    # Uses the JHE override (core/templates/account/email/login_code_message.txt),
    # not allauth's default "Your sign-in code is listed below" wording.
    assert "JupyterHealth Exchange sign-in code" in mail.outbox[0].body
    code_match = re.search(r"[A-Z0-9]{3,}-[A-Z0-9]{3,}", mail.outbox[0].body)
    assert code_match, mail.outbox[0].body
    code = code_match.group(0)

    # 3. Confirming the code logs the patient in and resumes the OAuth `next`.
    confirm = client.post(request_code.url, {"action": "verify", "code": code})
    assert confirm.status_code == 302
    assert str(client.session["_auth_user_id"]) == str(patient_user.pk)


def test_request_code_page_uses_jhe_override_template(client, db):
    # The override (core/templates/account/request_login_code.html) extends the
    # JHE accounts layout; allauth's default does not. Assert on the template
    # name + stable markup rather than copy, which is tweaked freely.
    resp = client.get(reverse("login-otp"))
    assert resp.status_code == 200
    assert "account/request_login_code.html" in {t.name for t in resp.templates}
    assert b'name="email"' in resp.content


def test_confirm_code_page_uses_jhe_override_template(client, patient_user):
    set_patient_access_clients([PATIENT_CLIENT_ID])
    # Initiate a code so the confirm stage exists in the session.
    client.post(reverse("login-otp"), {"email": patient_user.email})
    resp = client.get(reverse("account_confirm_login_code"))
    assert resp.status_code == 200
    assert "account/confirm_login_code.html" in {t.name for t in resp.templates}
    assert b'name="code"' in resp.content
    # The recipient email is surfaced on the page.
    assert patient_user.email.encode() in resp.content


def test_code_sent_message_is_reworded(client, patient_user):
    # Override of account/messages/login_code_sent.txt.
    set_patient_access_clients([PATIENT_CLIENT_ID])
    client.post(reverse("login-otp"), {"email": patient_user.email})
    resp = client.get(reverse("account_confirm_login_code"))
    # Apostrophe-free substring so it matches regardless of HTML escaping.
    assert b"sent a sign-in code to" in resp.content


def test_unknown_email_uses_jhe_override_message(client, db):
    # Enumeration protection: an unknown email still "succeeds" (redirects to the
    # confirm step) but receives the unknown-account email instead of a code.
    # Override of account/email/unknown_account_message.txt.
    resp = client.post(reverse("login-otp"), {"email": "nobody@example.org"})
    assert resp.status_code == 302
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "nobody@example.org" in body
    assert "we don't have an account on record" in body
    assert "we do not have any record of such an account" not in body


def test_incorrect_code_message_is_reworded(client, patient_user):
    # JheAccountAdapter rewords adapter.error_messages["incorrect_code"].
    set_patient_access_clients([PATIENT_CLIENT_ID])
    client.post(reverse("login-otp"), {"email": patient_user.email})
    resp = client.post(
        reverse("account_confirm_login_code"),
        {"action": "verify", "code": "ZZZZ-ZZZZ"},
    )
    assert resp.status_code == 200
    assert b"That sign-in code is incorrect" in resp.content
    assert b"Incorrect code." not in resp.content
