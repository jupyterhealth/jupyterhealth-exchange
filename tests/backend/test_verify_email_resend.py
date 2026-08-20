"""The verify-email resend view must actually send the email (it redirects to
"check your email" either way, so a silent no-op is invisible to the user)."""

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from core.models import JheUser


class VerifyEmailResendTests(TestCase):
    def setUp(self):
        self.user = JheUser.objects.create_user(
            email="unverified@example.org", password="hunter2!", user_type="practitioner"
        )
        self.client.force_login(self.user)

    def test_post_sends_verification_email(self):
        response = self.client.post(reverse("verify_email"))
        self.assertRedirects(response, reverse("verify_email_done"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        # The email must actually carry the verification link, not just be sent —
        # a resend with no confirm URL would leave the user unable to verify.
        self.assertIn("/accounts/verify_email_confirm/", mail.outbox[0].body)

    def test_post_already_verified_sends_nothing(self):
        self.user.email_is_verified = True
        self.user.save(update_fields=["email_is_verified"])
        response = self.client.post(reverse("verify_email"))
        self.assertRedirects(response, reverse("verify_email_complete"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)
