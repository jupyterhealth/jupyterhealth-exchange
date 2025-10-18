"""
OAuth 2.0 authorization flow with PKCE and private_key_jwt support
"""

import secrets
import hashlib
import base64
import webbrowser
import time
import threading
import jwt
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, parse_qs, urlparse
from typing import Optional
import requests

from config import (
    JHE_AUTHORIZE_URL,
    JHE_TOKEN_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    OIDC_RSA_PRIVATE_KEY,
    REDIRECT_URI,
    CALLBACK_PORT,
)
from .token_cache import TokenCache


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback"""

    authorization_code = None
    state_received = None
    id_token = None  # Store ID token for browser polling

    def do_GET(self):
        """Handle OAuth callback"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/token":
            # Endpoint for browser to poll for ID token
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            import json

            if OAuthCallbackHandler.id_token:
                response_data = {"id_token": OAuthCallbackHandler.id_token}
            else:
                response_data = {"id_token": None}

            self.wfile.write(json.dumps(response_data).encode())

        elif parsed.path == "/callback":
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
                        <p>Exchanging authorization code for tokens...</p>
                        <p id="status">⏳ Please wait, fetching ID token...</p>
                        <div id="token-display" style="display:none; margin-top: 30px; text-align: left; max-width: 800px; margin-left: auto; margin-right: auto;">
                            <h2>🎯 ID Token Captured!</h2>
                            <p><strong>Open your browser console (F12) to see the full token and decoded payload.</strong></p>
                            <div style="background: #f5f5f5; padding: 15px; border-radius: 5px; margin-top: 15px; word-wrap: break-word;">
                                <p style="font-size: 12px; color: #666;">Raw JWT (first 100 chars):</p>
                                <code id="token-preview" style="font-size: 11px;"></code>
                            </div>
                            <p style="margin-top: 20px; color: #28a745;"><strong>✓ Check browser console for full token and instructions!</strong></p>
                        </div>
                        <script>
                            // Poll for ID token from callback server
                            let pollCount = 0;
                            const maxPolls = 20; // 10 seconds max

                            function pollForToken() {
                                fetch('/token')
                                    .then(res => res.json())
                                    .then(data => {
                                        if (data.id_token) {
                                            displayToken(data.id_token);
                                        } else if (pollCount < maxPolls) {
                                            pollCount++;
                                            setTimeout(pollForToken, 500);
                                        } else {
                                            document.getElementById('status').innerHTML = '⚠️ Token exchange timeout. Check terminal for token.';
                                        }
                                    })
                                    .catch(err => {
                                        if (pollCount < maxPolls) {
                                            pollCount++;
                                            setTimeout(pollForToken, 500);
                                        } else {
                                            document.getElementById('status').innerHTML = '⚠️ Could not fetch token. Check terminal for token.';
                                        }
                                    });
                            }

                            function displayToken(idToken) {
                                // Log to console with formatting
                                console.log("\\n" + "=".repeat(80));
                                console.log("🎯 ID TOKEN CAPTURED - Ready for jwt.io demonstration!");
                                console.log("=".repeat(80));
                                console.log("\\nRAW JWT TOKEN (copy everything below):");
                                console.log("-".repeat(80));
                                console.log(idToken);
                                console.log("-".repeat(80));

                                // Decode and display
                                try {
                                    const parts = idToken.split('.');
                                    const payload = JSON.parse(atob(parts[1]));

                                    console.log("\\nDECODED PAYLOAD (what you'll see at jwt.io):");
                                    console.log("-".repeat(80));
                                    console.log(JSON.stringify(payload, null, 2));
                                    console.log("-".repeat(80));

                                    if (payload.jhe_permissions) {
                                        const perms = payload.jhe_permissions;
                                        console.log("\\n✨ CUSTOM CLAIMS FOUND:");
                                        console.log("   - user_type:", payload.user_type);
                                        console.log("   - user_id:", payload.user_id);
                                        console.log("   - studies:", (perms.studies || []).length, "accessible");
                                        console.log("   - organizations:", (perms.organizations || []).length, "memberships");
                                    }
                                } catch (e) {
                                    console.error("Could not decode token:", e);
                                }

                                console.log("\\n📋 TO VERIFY AT JWT.IO:");
                                console.log("   1. Copy the RAW JWT TOKEN above");
                                console.log("   2. Open https://jwt.io in your browser");
                                console.log("   3. Paste into the 'Encoded' text box");
                                console.log("   4. See the decoded payload on the right");
                                console.log("   5. Look for 'jhe_permissions' in the payload!");
                                console.log("=".repeat(80));

                                // Update UI
                                document.getElementById('status').style.display = 'none';
                                document.getElementById('token-display').style.display = 'block';
                                document.getElementById('token-preview').textContent = idToken.substring(0, 100) + '...';
                            }

                            // Start polling after a short delay
                            setTimeout(pollForToken, 1000);
                        </script>
                    </body>
                </html>
                """
                self.wfile.write(success_html.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging"""
        pass


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


def create_client_assertion() -> Optional[str]:
    """
    Create JWT client assertion for private_key_jwt authentication

    Returns:
        JWT string if private key is configured, None otherwise
    """
    if not OIDC_RSA_PRIVATE_KEY:
        return None

    # Create JWT claims
    now = int(time.time())
    claims = {
        "iss": CLIENT_ID,
        "sub": CLIENT_ID,
        "aud": JHE_TOKEN_URL,
        "jti": secrets.token_urlsafe(16),
        "exp": now + 300,  # 5 minutes
        "iat": now,
    }

    # Sign with RSA private key
    try:
        client_assertion = jwt.encode(claims, OIDC_RSA_PRIVATE_KEY, algorithm="RS256")
        return client_assertion
    except Exception as e:
        print(f"Error creating client assertion: {e}")
        return None


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
    callback_server = start_callback_server()

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

            # ========== CAPTURE ID TOKEN FOR DEMONSTRATION ==========
            if "id_token" in token:
                # Store ID token for browser polling
                OAuthCallbackHandler.id_token = token["id_token"]
                id_token_jwt = token["id_token"]

                print("\n" + "=" * 80)
                print("🎯 ID TOKEN CAPTURED - Ready for jwt.io demonstration!")
                print("=" * 80)
                print("\nRAW JWT TOKEN (copy everything below the line):")
                print("-" * 80)
                print(id_token_jwt)
                print("-" * 80)

                # Decode and show payload
                try:
                    decoded = jwt.decode(id_token_jwt, options={"verify_signature": False})
                    print("\nDECODED PAYLOAD (what you'll see at jwt.io):")
                    print("-" * 80)
                    import json

                    print(json.dumps(decoded, indent=2))
                    print("-" * 80)

                    # Highlight custom claims
                    if "jhe_permissions" in decoded:
                        perms = decoded["jhe_permissions"]
                        print(f"\n✨ CUSTOM CLAIMS FOUND:")
                        print(f"   - user_type: {decoded.get('user_type')}")
                        print(f"   - user_id: {decoded.get('user_id')}")
                        print(f"   - studies: {len(perms.get('studies', []))} accessible")
                        print(f"   - organizations: {len(perms.get('organizations', []))} memberships")
                except Exception as e:
                    print(f"\n⚠️  Could not decode ID token: {e}")

                print("\n📋 TO VERIFY AT JWT.IO:")
                print("   1. Copy the RAW JWT TOKEN above (the long string)")
                print("   2. Open https://jwt.io in your browser")
                print("   3. Paste into the 'Encoded' text box on the left")
                print("   4. See the decoded payload on the right")
                print("   5. Look for 'jhe_permissions' in the payload!")
                print("=" * 80 + "\n")
            # ========== END CAPTURE ==========

            # Save token
            TokenCache.save_token(token)

            print("✓ Authentication successful! Token cached.")
            print("=" * 60 + "\n")

            return token
        else:
            print(f"❌ Token exchange failed: {response.status_code}")
            print(f"Response: {response.text}")
            return None
    except Exception as e:
        print(f"❌ Error exchanging token: {e}")
        return None


def get_valid_token() -> Optional[str]:
    """
    Get a valid access token, performing OAuth flow if necessary

    Returns:
        Access token string or None
    """
    # Try to load cached token
    token = TokenCache.get_valid_token()

    if token:
        return token

    # No valid cached token, perform OAuth flow
    print("\n⚠️  No valid token found. Starting authentication flow...")
    token_data = perform_oauth_flow()

    if token_data and "access_token" in token_data:
        return token_data["access_token"]

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
