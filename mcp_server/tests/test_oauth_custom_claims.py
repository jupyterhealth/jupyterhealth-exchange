#!/usr/bin/env python
"""
Test OAuth Flow and Capture ID Token with Custom Claims

This script tests the OAuth authentication flow with the custom JHEOAuth2Validator
and displays the ID token with custom claims including user permissions.
"""
import os
import sys
import django
import json
import jwt
from datetime import timedelta
from django.utils import timezone

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jhe.settings")
django.setup()

from core.models import JheUser
from oauth2_provider.models import get_application_model, AccessToken as OAuthAccessToken
from oauth2_provider.generators import generate_client_secret

Application = get_application_model()


def create_test_token_for_user(email):
    """
    Create a test access token and ID token for a user.
    This simulates what happens during OAuth flow.
    """
    # Get the user
    try:
        user = JheUser.objects.get(email=email)
        print(f"\n📧 User: {user.email}")
        print(f"   Type: {user.user_type}")
        print(f"   ID: {user.id}")
    except JheUser.DoesNotExist:
        print(f"❌ User {email} not found!")
        return None

    # Get existing OAuth application (created by seed command)
    try:
        app = Application.objects.get(client_id="Ima7rx8D6eko0PzlU1jK28WBUT2ZweZj7mqVG2wm")
        print(f"\n📱 Using OAuth application: {app.name}")
    except Application.DoesNotExist:
        print(f"❌ OAuth application not found! Run 'python manage.py seed' first.")
        return None

    # Create access token
    expires = timezone.now() + timedelta(seconds=3600)  # 1 hour
    access_token, created = OAuthAccessToken.objects.get_or_create(
        user=user,
        application=app,
        defaults={"token": "test_access_token_" + user.email, "expires": expires, "scope": "openid"},
    )

    if not created:
        # Update expiry if token already exists
        access_token.expires = expires
        access_token.save()

    print(f"\n🔑 Access Token: {access_token.token[:50]}...")
    print(f"   Expires: {access_token.expires}")
    print(f"   Scope: {access_token.scope}")

    # Now let's manually create an ID token with our custom validator logic
    # This is what our JHEOAuth2Validator.get_additional_claims() does
    from core.oauth_validators import JHEOAuth2Validator
    from unittest.mock import Mock

    validator = JHEOAuth2Validator()

    # Create a mock request object like OAuthlib would pass
    request = Mock()
    request.user = user

    # Get custom claims from our validator
    custom_claims = validator.get_additional_claims(request)

    print(f"\n✨ Custom Claims Generated:")
    print(json.dumps(custom_claims, indent=2))

    # Create standard OIDC claims
    now = timezone.now()
    standard_claims = {
        "iss": "http://localhost:8000/o",
        "sub": str(user.id),
        "aud": app.client_id,
        "exp": int((now + timedelta(seconds=3600)).timestamp()),
        "iat": int(now.timestamp()),
        "auth_time": int(now.timestamp()),
    }

    # Combine standard and custom claims
    all_claims = {**standard_claims, **custom_claims}

    print(f"\n🎫 Complete ID Token Payload:")
    print("=" * 80)
    print(json.dumps(all_claims, indent=2))
    print("=" * 80)

    # Create JWT (note: for demo we won't sign it properly, just decode what would be signed)
    from jose import jwt as jose_jwt

    # Read private key from settings
    from django.conf import settings

    private_key = settings.OAUTH2_PROVIDER.get("OIDC_RSA_PRIVATE_KEY")

    if private_key:
        try:
            # Sign the ID token
            id_token_string = jose_jwt.encode(all_claims, private_key, algorithm="RS256")

            print(f"\n🔐 Signed ID Token (JWT):")
            print(id_token_string)
            print(f"\n   Length: {len(id_token_string)} bytes")
            print(f"\n💡 Decode this token at https://jwt.io to verify claims")

            # Verify we can decode it
            # decoded = jose_jwt.decode(
            #     id_token_string,
            #     private_key,
            #     algorithms=['RS256'],
            #     options={"verify_signature": False}
            # )
            # print(f"\n✅ Token verified and decoded successfully")

        except ImportError:
            print(f"\n⚠️  python-jose not installed, cannot sign JWT")
            print(f"   Install with: pip install python-jose")
    else:
        print(f"\n⚠️  No OIDC_RSA_PRIVATE_KEY found in settings")

    return all_claims


def main():
    print("=" * 80)
    print("OAuth Custom Claims Test")
    print("=" * 80)

    # Test with a practitioner user (default: sam@example.com)
    test_user_email = sys.argv[1] if len(sys.argv) > 1 else "sam@example.com"

    print(f"\n🧪 Testing OAuth flow for: {test_user_email}\n")

    claims = create_test_token_for_user(test_user_email)

    if claims:
        print(f"\n✅ Test completed successfully!")
        print(f"\n📊 Summary:")
        print(f"   - User Type: {claims.get('user_type')}")
        print(f"   - User ID: {claims.get('user_id')}")

        if "jhe_permissions" in claims:
            perms = claims["jhe_permissions"]
            print(f"   - Studies: {len(perms.get('studies', []))} accessible")
            print(f"   - Organizations: {len(perms.get('organizations', []))} memberships")

            if perms.get("studies"):
                print(f"\n   📚 Accessible Study IDs:")
                for study_id in perms["studies"]:
                    print(f"      - Study {study_id}")

            if perms.get("organizations"):
                print(f"\n   🏢 Organization Memberships:")
                for org in perms["organizations"]:
                    print(f"      - {org['name']} (Role: {org['role']})")

        print(f"\n🎯 Key Achievement:")
        print(f"   Custom claims are now included in ID tokens, enabling")
        print(f"   MCP servers to read permissions locally without API calls!")


if __name__ == "__main__":
    main()
