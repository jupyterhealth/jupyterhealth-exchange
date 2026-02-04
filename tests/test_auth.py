import json
import time
from urllib.parse import urldecode

from django.conf import settings
from django.test import TestCase

import responses

from core.models import (
    JheUser,
)


class AuthTest(TestCase):

    def userinfo_response(self, request):
        token = request.headers["Authorization"].split()[1]
        headers = {"Content-Type": "application/json"}
        if token == "practitioner-token":
            return (
                200,
                headers,
                json.dumps(
                    {
                        "sub": self.practitioner_id,
                        "profile": f"Practitioner/{self.practitioner_id}",
                        "name": "Practitioner Name",
                    }
                ),
            )
        elif token == "patient-token":
            return (
                200,
                headers,
                json.dumps(
                    {
                        "sub": self.patient_id,
                        "profile": f"Patient/{self.patient_id}",
                        "name": "Patient Name",
                    }
                ),
            )
        elif token == "other-token":
            return (
                200,
                headers,
                json.dumps(
                    {
                        "sub": "other-sub-id",
                        "profile": "Practitioner/other-sub-id",
                        "name": "Practitioner Name",
                    }
                ),
            )
        else:
            return 403, headers, json.dumps({"error": "Not a valid token"})

    def introspection_response(self, request):
        token = urldecode(request.body)["token"][0]

        headers = {"Content-Type": "application/json"}

        # sample introspection from medplum
        # (excluding 'sub')
        introspection = {
            "active": True,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "iss": settings.TRUSTED_TOKEN_IDP,
            "client_id": "abc-123",
            "scope": "user/*.* patient/*.read openid profile launch launch/patient",
            "patient": "01961612-dbdc-759b-b885-f55117556bb6",
        }

        if token == "practitioner-token":
            response = {"sub": self.practitioner_id}
            response.update(introspection)
            return 200, headers, json.dumps(response)
        elif token == "patient-token":
            response = {"sub": self.patient_id}
            response.update(introspection)
            return 200, headers, json.dumps(response)
        else:
            return 200, headers, json.dumps({"active": False})

    def setUp(self):
        self.idp_url = idp_url = "https://example.localhost"

        self.practitioner_id = "practitioner-c56c"
        self.patient_id = "patient-1fbe0b2f"
        self.practitioner_user = JheUser.objects.create_user(
            email="practitioner-user@example.com",
            password="password",
            identifier=self.practitioner_id,
            user_type="practitioner",
        )
        self.patient_user = JheUser.objects.create_user(
            email="patient-user@example.com",
            password="password",
            identifier=self.patient_id,
            user_type="patient",
        )

        settings.TRUSTED_TOKEN_IDP = idp_url
        self.oidc_config = oidc_config = {
            "authorization_endpoint": f"{idp_url}/oauth2/authorize",
            "token_endpoint": f"{idp_url}/oauth2/token",
            "userinfo_endpoint": f"{idp_url}/oauth2/userinfo",
            "introspection_endpoint": f"{idp_url}/oauth2/introspect",
        }

        responses.add_callback(
            responses.GET,
            oidc_config["userinfo_endpoint"],
            callback=self.userinfo_response,
            content_type="application/json",
        )

        responses.add_callback(
            responses.POST,
            oidc_config["introspection_endpoint"],
            callback=self.introspection_response,
            content_type="application/json",
        )

        responses.get(
            f"{idp_url}/.well-known/openid-configuration",
            body=json.dumps(oidc_config),
            content_type="application/json",
        )
        responses.start()

    def tearDown(self):
        responses.stop()
        responses.reset()

    request_fields = {
        "audience": settings.SITE_URL,
        "iss": "https://example.localhost",
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
    }

    def test_token_exchange(self):
        request_data = {}
        request_data.update(self.request_fields)
        request_data["subject_token"] = "practitioner-token"

        response = self.client.post(
            "/o/token-exchange",
            data=request_data,
        )
        token_info = response.json()
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(token_info["expires_in"], 0)
        self.assertEqual(token_info["scope"], "openid")
        self.assertEqual(token_info["token_type"], "Bearer")
        self.assertEqual(token_info["issued_token_type"], "urn:ietf:params:oauth:token-type:access_token")
        access_token = token_info["access_token"]
        r = self.client.get("/api/v1/users/profile", headers={"Authorization": f"Bearer {access_token}"})
        self.assertEqual(r.status_code, 200)
        user_info = r.json()
        self.assertEqual(user_info["id"], self.practitioner_user.id)

    def test_missing_arguments(self):
        response = self.client.post(
            "/o/token-exchange",
            data={"subject_token": "patient-id"},
        )
        info = response.json()
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing required argument", info["error"])

    def test_audience_mismatch(self):
        request_data = {}
        request_data.update(self.request_fields)
        request_data["subject_token"] = "practitioner-token"
        request_data["audience"] = "https://jhe"
        response = self.client.post(
            "/o/token-exchange",
            data=request_data,
        )
        info = response.json()
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(info["error"], "audience must be http://localhost:8000, not https://jhe")

    def test_idp_mismatch(self):
        request_data = {}
        request_data.update(self.request_fields)
        request_data["subject_token"] = "practitioner-token"
        request_data["iss"] = "https://not-ours"
        response = self.client.post(
            "/o/token-exchange",
            data=request_data,
        )
        info = response.json()
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Can only exchange tokens from trusted issuer", info["error"])

    def test_patient_not_practitioner(self):
        request_data = {}
        request_data.update(self.request_fields)
        request_data["subject_token"] = "patient-token"
        response = self.client.post(
            "/o/token-exchange",
            data=request_data,
        )
        info = response.json()
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Practitioner not found", info["error"])

    def test_practitioner_not_found(self):
        request_data = {}
        request_data.update(self.request_fields)
        request_data["subject_token"] = "other-token"
        response = self.client.post(
            "/o/token-exchange",
            data=request_data,
        )
        info = response.json()
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Practitioner not found", info["error"])

    def test_invalid_token(self):
        request_data = {}
        request_data.update(self.request_fields)
        request_data["subject_token"] = "no-such-token"
        response = self.client.post(
            "/o/token-exchange",
            data=request_data,
        )
        info = response.json()
        self.assertEqual(response.headers["Content-Type"], "application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Token not found", info["error"])
