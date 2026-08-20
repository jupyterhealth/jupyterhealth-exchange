"""JHE glue around allauth's SAML provider: the login-button gate, the
practitioner-provisioning adapter, and the ACS login flows (#697)."""

from unittest import mock

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.adapters import JheSocialAccountAdapter
from core.models import JheSetting, JheUser, Practitioner

# Minimal-but-valid SocialApp settings: the idp block is mandatory for
# build_saml_config, and allow_single_label_domains lets python3-saml's strict
# mode accept the test client's "testserver" host.
IDP_SETTINGS = {
    "idp": {"entity_id": "https://idp.example.org/metadata", "sso_url": "https://idp.example.org/sso", "x509cert": ""},
    "advanced": {"allow_single_label_domains": True},
}


def _set_setting(key, value_type, value):
    setting, _ = JheSetting.objects.update_or_create(key=key, defaults={"value_type": value_type})
    setting.set_value(value_type, value)
    setting.save()


class SamlUrlsTests(TestCase):
    """The SSO endpoints are the SP's external contract — IdPs register them."""

    def test_saml_endpoints_route(self):
        self.assertEqual(reverse("saml_login", kwargs={"organization_slug": "idp"}), "/allauth/saml/idp/login/")
        self.assertEqual(reverse("saml_acs", kwargs={"organization_slug": "idp"}), "/allauth/saml/idp/acs/")
        self.assertEqual(reverse("saml_metadata", kwargs={"organization_slug": "idp"}), "/allauth/saml/idp/metadata/")

    def test_metadata_endpoint_serves_sp_descriptor(self):
        """Exercises SocialApp JSON -> build_saml_config -> python3-saml/xmlsec."""
        SocialApp.objects.create(provider="saml", name="Test IdP", client_id="idp", settings=IDP_SETTINGS)
        response = self.client.get("/allauth/saml/idp/metadata/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("xml", response["Content-Type"])
        self.assertIn(b"EntityDescriptor", response.content)
        self.assertIn(b"/allauth/saml/idp/acs/", response.content)


class LoginPageSamlButtonTests(TestCase):
    """The SAML button requires the auth.sso.saml2 flag AND exactly one
    SocialApp — with zero or several, provider_login_url would 500 the whole
    login page, so the button degrades to hidden instead."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_button_hidden_when_disabled(self):
        _set_setting("auth.sso.saml2", "int", 0)
        response = self.client.get("/accounts/login/")
        self.assertNotContains(response, "Continue with SAML2")

    def test_button_links_to_allauth_when_enabled(self):
        SocialApp.objects.create(provider="saml", name="Test IdP", client_id="idp")
        _set_setting("auth.sso.saml2", "int", 1)
        response = self.client.get("/accounts/login/")
        self.assertContains(response, "Continue with SAML2")
        self.assertContains(response, "/allauth/saml/idp/login/")
        # The button carries the post-login destination through the query string:
        # default it to the admin org list (matches the password form's next).
        self.assertContains(response, "next=%2Fclients%2Fjhe-admin%2Forganizations")

    def test_button_forwards_login_page_next(self):
        SocialApp.objects.create(provider="saml", name="Test IdP", client_id="idp")
        _set_setting("auth.sso.saml2", "int", 1)
        response = self.client.get("/accounts/login/?next=/clients/jhe-admin/studies")
        # A ?next= on the login page overrides the default and reaches the href,
        # so SSO users land where a same-page password login would have.
        self.assertContains(response, "next=%2Fclients%2Fjhe-admin%2Fstudies")

    def test_button_hidden_when_enabled_without_social_app(self):
        _set_setting("auth.sso.saml2", "int", 1)
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Continue with SAML2")

    def test_button_hidden_when_multiple_social_apps(self):
        SocialApp.objects.create(provider="saml", name="IdP A", client_id="a")
        SocialApp.objects.create(provider="saml", name="IdP B", client_id="b")
        _set_setting("auth.sso.saml2", "int", 1)
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Continue with SAML2")


class JheSocialAccountAdapterTests(TestCase):
    """SAML logins provision practitioners, optionally domain-restricted."""

    def setUp(self):
        cache.clear()
        self.adapter = JheSocialAccountAdapter()
        self.request = RequestFactory().get("/")

    def tearDown(self):
        cache.clear()

    def _sociallogin(self, email, uid="idp-uid-1"):
        user = JheUser(email=email)
        return SocialLogin(user=user, account=SocialAccount(provider="saml", uid=uid))

    def test_populate_user_provisions_practitioner(self):
        sociallogin = self._sociallogin("doc@example.org")
        user = self.adapter.populate_user(self.request, sociallogin, {"email": "doc@example.org"})
        self.assertEqual(user.user_type, "practitioner")
        self.assertEqual(user.identifier, "idp-uid-1")

    def test_saved_signup_creates_practitioner_profile(self):
        """The composition the adapter relies on: save_user persists the user
        and JheUser.save() auto-creates the Practitioner profile."""
        SessionMiddleware(lambda r: None).process_request(self.request)
        sociallogin = self._sociallogin("newdoc@example.org")
        sociallogin.user = self.adapter.populate_user(self.request, sociallogin, {"email": "newdoc@example.org"})
        user = self.adapter.save_user(self.request, sociallogin, form=None)
        self.assertTrue(Practitioner.objects.filter(jhe_user=user).exists())

    def test_signup_open_without_domain_restriction(self):
        self.assertTrue(self.adapter.is_open_for_signup(self.request, self._sociallogin("doc@anywhere.net")))

    def test_signup_closed_without_email(self):
        """Misconfigured IdP attribute mapping must not fall through to the
        type-any-email signup form."""
        self.assertFalse(self.adapter.is_open_for_signup(self.request, self._sociallogin("")))

    def test_signup_respects_valid_domains(self):
        _set_setting("auth.sso.valid_domains", "string", "example.org, @hospital.edu")
        self.assertTrue(self.adapter.is_open_for_signup(self.request, self._sociallogin("doc@EXAMPLE.org")))
        self.assertTrue(self.adapter.is_open_for_signup(self.request, self._sociallogin("doc@hospital.edu")))
        self.assertFalse(self.adapter.is_open_for_signup(self.request, self._sociallogin("doc@evil.com")))


class SamlAcsFlowTests(TestCase):
    """End-to-end ACS handling with a mocked python3-saml auth object
    (allauth's own testing approach; signature validation is python3-saml's
    job and the metadata test already exercises the real xmlsec chain).
    Covers the flows the adapter unit tests can't reach: existing-account
    email-authentication, provisioning, and the rejection renders."""

    EMAIL_ATTR = "urn:oid:0.9.2342.19200300.100.1.3"

    def setUp(self):
        cache.clear()
        SocialApp.objects.create(
            provider="saml",
            name="Test IdP",
            client_id="idp",
            settings={
                **IDP_SETTINGS,
                # Per-IdP opt-ins (see jhe/settings.py): trust asserted emails,
                # enable email-authentication into existing accounts.
                "verified_email": True,
                "email_authentication": True,
                # Skip the SP-initiated state handshake so the test can post an
                # (otherwise-rejected) unsolicited assertion straight to ACS.
                "advanced": {**IDP_SETTINGS["advanced"], "reject_idp_initiated_sso": False},
            },
        )

    def tearDown(self):
        cache.clear()

    def _post_acs(self, email, uid="idp-uid-1"):
        auth = mock.Mock()
        auth.process_response.return_value = None
        auth.get_errors.return_value = []
        auth.is_authenticated.return_value = True
        auth.get_attributes.return_value = {self.EMAIL_ATTR: [email]}
        auth.get_nameid.return_value = uid
        auth.get_nameid_format.return_value = ""
        auth.get_last_response_in_response_to.return_value = None
        with mock.patch("allauth.socialaccount.providers.saml.views.build_auth", return_value=auth):
            return self.client.post("/allauth/saml/idp/acs/", {"SAMLResponse": "irrelevant"}, follow=True)

    def _logged_in_user_id(self):
        return self.client.session.get("_auth_user_id")

    def test_acs_provisions_new_practitioner(self):
        self._post_acs("newdoc@example.org")
        user = JheUser.objects.get(email="newdoc@example.org")
        self.assertEqual(str(user.pk), self._logged_in_user_id())
        self.assertEqual(user.user_type, "practitioner")
        self.assertTrue(Practitioner.objects.filter(jhe_user=user).exists())
        self.assertTrue(user.email_is_verified)
        self.assertEqual(SocialAccount.objects.get(user=user, provider="saml").uid, "idp-uid-1")

    def test_acs_existing_practitioner_keeps_password(self):
        """Email-authentication must not trip allauth's password wipe: the
        pre_social_login backfill records the verified EmailAddress row that
        practitioners created outside allauth never got."""
        user = JheUser.objects.create_user("doc@example.org", password="hunter2!", user_type="practitioner")
        self._post_acs("doc@example.org")
        user.refresh_from_db()
        self.assertEqual(str(user.pk), self._logged_in_user_id())
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("hunter2!"))
        self.assertTrue(SocialAccount.objects.filter(user=user, provider="saml").exists())
        self.assertTrue(EmailAddress.objects.filter(user=user, email="doc@example.org", verified=True).exists())

    def test_acs_inactive_practitioner_not_logged_in(self):
        """Deactivation revokes SAML access (the adapter's documented way to
        cut off a user): an is_active=False practitioner reaching ACS by
        email-authentication must not receive a session."""
        JheUser.objects.create_user("doc@example.org", password="hunter2!", user_type="practitioner", is_active=False)
        self._post_acs("doc@example.org")
        self.assertIsNone(self._logged_in_user_id())

    def test_acs_existing_patient_rejected(self):
        """SAML is a practitioner entrance: a same-email patient account is
        rejected before login — and before the password wipe."""
        user = JheUser.objects.create_user("pat@example.org", password="hunter2!", user_type="patient")
        response = self._post_acs("pat@example.org")
        user.refresh_from_db()
        self.assertTemplateUsed(response, "account/signup_closed.html")
        self.assertIsNone(self._logged_in_user_id())
        self.assertTrue(user.check_password("hunter2!"))
        self.assertFalse(SocialAccount.objects.filter(user=user).exists())

    def test_acs_rejected_domain_renders_signup_closed(self):
        _set_setting("auth.sso.valid_domains", "string", "hospital.edu")
        response = self._post_acs("doc@evil.com")
        self.assertTemplateUsed(response, "account/signup_closed.html")
        self.assertIsNone(self._logged_in_user_id())
        self.assertFalse(JheUser.objects.filter(email="doc@evil.com").exists())
