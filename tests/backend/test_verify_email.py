"""Anonymous POST /accounts/verify_email/ must not 500 (#717)."""

from django.conf import settings
from django.urls import reverse


def test_anonymous_verify_email_post_redirects_to_login(client):
    response = client.post(reverse("verify_email"))
    assert response.status_code == 302
    assert response.url.startswith(settings.LOGIN_URL)
    assert "next=" in response.url


def test_anonymous_verify_email_get_redirects_to_login(client):
    response = client.get(reverse("verify_email"))
    assert response.status_code == 302
    assert response.url.startswith(settings.LOGIN_URL)
