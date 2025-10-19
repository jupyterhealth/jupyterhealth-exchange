"""
OAuth 2.0 authorization flow with PKCE and private_key_jwt support
"""

import secrets
import hashlib
import base64
import json
import logging
import webbrowser
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, parse_qs, urlparse
from typing import Optional
import requests
from requests.exceptions import RequestException

from config import (
    JHE_AUTHORIZE_URL,
    JHE_TOKEN_URL,
    CLIENT_ID,
    REDIRECT_URI,
    CALLBACK_PORT,
)
from .token_cache import TokenCache

logger = logging.getLogger(__name__)


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback"""

    authorization_code = None
    state_received = None

    def do_GET(self):
        """Handle OAuth callback"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/callback":
            # Extract authorization code and state
            OAuthCallbackHandler.authorization_code = params.get("code", [None])[0]
            OAuthCallbackHandler.state_received = params.get("state", [None])[0]
            error = params.get("error", [None])[0]

            # Send response to browser
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()

            if error:
                error_html = f"""
                <html>
                    <head><title>JHE MCP - Authentication Failed</title></head>
                    <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                        <h1>❌ Authentication Failed</h1>
                        <p>Error: {error}</p>
                        <p>Please close this window and try again.</p>
                    </body>
                </html>
                """
                self.wfile.write(error_html.encode())
            else:
                success_html = """
                <html>
                    <head><title>JHE MCP - Authentication Success</title></head>
                    <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                        <h1>✅ Authentication Successful!</h1>
                        <p>You have been successfully authenticated.</p>
                        <p style="margin-top: 30px; color: #28a745;"><strong>You can now close this browser window.</strong></p>
                    </body>
                </html>
                """
                self.wfile.write(success_html.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging"""


def pkce_challenge_from_verifier(verifier: str) -> str:
    """
    Generate PKCE code challenge from verifier

    Args:
        verifier: Random string (43-128 characters)

    Returns:
        Base64-URL-encoded SHA256 hash of verifier
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start_callback_server() -> HTTPServer:
    """
    Start local HTTP server for OAuth callback

    Returns:
        HTTPServer instance
    """
    server = HTTPServer(("localhost", CALLBACK_PORT), OAuthCallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return server


def perform_oauth_flow() -> Optional[dict]:
    """
    Perform OAuth 2.0 authorization code flow with PKCE

    Returns:
        Token data if successful, None otherwise
    """
    # Reset class variables
    OAuthCallbackHandler.authorization_code = None
    OAuthCallbackHandler.state_received = None

    # Generate PKCE verifier and challenge
    code_verifier = secrets.token_urlsafe(64)[:128]
    code_challenge = pkce_challenge_from_verifier(code_verifier)

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Build authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid",  # Only scope supported by JHE IdP
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    auth_url = f"{JHE_AUTHORIZE_URL}?{urlencode(auth_params)}"

    print("\n" + "=" * 60)
    print("🔐 JupyterHealth Exchange Authentication Required")
    print("=" * 60)
    print("\nOpening browser for authentication...")
    print(f"If browser doesn't open, visit:\n{auth_url}\n")

    # Start local callback server
    start_callback_server()

    # Open browser
    webbrowser.open(auth_url)

    # Wait for callback (timeout after 5 minutes)
    print("Waiting for authentication... (timeout: 5 minutes)")
    timeout = 300  # 5 minutes
    start_time = time.time()

    while OAuthCallbackHandler.authorization_code is None:
        if time.time() - start_time > timeout:
            print("❌ Authentication timeout")
            return None
        time.sleep(0.5)

    # Verify state
    if OAuthCallbackHandler.state_received != state:
        print("❌ State mismatch - possible CSRF attack")
        return None

    auth_code = OAuthCallbackHandler.authorization_code

    # Exchange authorization code for token
    print("✓ Authorization code received, exchanging for token...")

    token_data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
    }

    # Use PKCE only (public client - no client_secret or private_key_jwt)
    # JHE supports public OAuth clients with PKCE
    print("Using public client with PKCE")

    try:
        response = requests.post(JHE_TOKEN_URL, data=token_data, timeout=10)

        if response.status_code == 200:
            token = response.json()

            # Save token
            TokenCache.save_token(token)

            print("\n✓ Authentication successful! You can close the browser window.")
            print("=" * 60 + "\n")

            return token
        else:
            logger.error(f"Token exchange failed: HTTP {response.status_code}")
            print(f"❌ Token exchange failed: {response.status_code}")
            print(f"Response: {response.text}")
            return None
    except RequestException as e:
        logger.error(f"Network error during token exchange: {e}")
        print(f"❌ Network error exchanging token: {e}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Invalid token response: {e}")
        print(f"❌ Invalid response from OAuth server: {e}")
        return None


def get_valid_tokens() -> Optional[tuple[str, Optional[str]]]:
    """
    Get valid access token and ID token, performing OAuth flow if necessary

    Returns:
        Tuple of (access_token, id_token) or None
        id_token may be None if not present in OAuth response
    """
    # Try to load cached tokens
    tokens = TokenCache.get_valid_tokens()

    if tokens:
        return tokens

    # No valid cached tokens, perform OAuth flow
    print("\n⚠️  No valid tokens found. Starting authentication flow...")
    token_data = perform_oauth_flow()

    if token_data and "access_token" in token_data:
        access_token = token_data["access_token"]
        id_token = token_data.get("id_token")
        return (access_token, id_token)

    return None
