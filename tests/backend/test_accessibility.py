"""WCAG 2.1/2.2 AA markup on the shared base and the account pages (RFC #833)."""

import re

import pytest
from django.template.loader import render_to_string

PAGES = [
    "/accounts/login/",
    "/accounts/login-otp/",
    "/accounts/signup/",
    "/accounts/password_reset/",
    "/accounts/verify_email_done",
    "/clients/ehr-patient-portal/",
]


def _input(html, name):
    return re.search(rf'<input[^>]*name="{name}"[^>]*>', html, re.S).group(0)


@pytest.mark.parametrize("path", PAGES)
def test_pages_declare_language_and_viewport(db, client, path):
    html = client.get(path).content.decode()
    assert '<html lang="en"' in html
    assert '<meta name="viewport" content="width=device-width, initial-scale=1"' in html


def test_loading_overlay_is_a_status_not_a_modal(db, client):
    html = client.get("/accounts/login/").content.decode()
    overlay = html.split('id="navLoadingOverlay"', 1)[1].split("</div>", 1)[0]
    assert 'role="status"' in overlay
    assert "aria-modal" not in overlay


@pytest.mark.parametrize(
    "path,name,token",
    [
        ("/accounts/login/", "username", "email"),
        ("/accounts/login/", "password", "current-password"),
        ("/accounts/login-otp/", "email", "email"),
        ("/accounts/signup/", "email", "email"),
        ("/accounts/signup/", "password", "new-password"),
        ("/accounts/signup/", "password2", "new-password"),
        ("/accounts/password_reset/", "email", "email"),
    ],
)
def test_account_fields_carry_autocomplete_tokens(db, client, path, name, token):
    assert f'autocomplete="{token}"' in _input(client.get(path).content.decode(), name)


def test_password_reset_confirm_fields_carry_new_password_tokens():
    html = render_to_string("registration/password_reset_confirm.html", {"validlink": True})
    for name in ("new_password1", "new_password2"):
        assert 'autocomplete="new-password"' in _input(html, name)


def test_account_pages_announce_client_errors_and_name_the_logo(db, client):
    html = client.get("/accounts/signup/").content.decode()
    site_title = re.search(r"<title>(.*?) - ", html).group(1)
    assert '<div id="clientErrors" role="alert"></div>' in html
    assert "--bs-border-color: var(--bs-gray-600);" in html  # 3:1 input border that leaves validation colors alone
    assert f'alt="{site_title}"' in html


def test_verify_email_done_uses_the_accounts_layout(db, client):
    html = client.get("/accounts/verify_email_done").content.decode()
    assert re.search(r"<title>.* - Verify Email</title>", html)
    assert '<h1 class="h3 mb-3 fw-normal">Check Your Email</h1>' in html
