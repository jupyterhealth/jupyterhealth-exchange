## Troubleshooting Local Development

### Issue: Blank Screen After Log In on Windows

**Symptom:**
After logging in, users are redirected to the portal, but a blank screen persists. This issue seems related to the `oidc-client-ts` library but is actually due to incorrectly set environment variables on Windows.

**Cause:**
On Windows systems (particularly when running Django via Visual Studio Code or Git Bash), the environment variables related to OIDC in `settings.py` may become incorrectly formatted. This causes URLs to be malformed, preventing proper authentication.

**Examples of Incorrectly Set Values:**
- `OIDC_CLIENT_REDIRECT_URI: http://localhost:8000C:/Program Files/Git/auth/callback`
- `OIDC_CLIENT_AUTHORITY: http://localhost:8000O://`

**Solution:**
Make sure the `SITE_URL` variable points to the exact host you use to access the app (for example `http://localhost:8000`). The settings now derive the authority and redirect URLs from that value using the fixed `/o/` and `/auth/callback` paths, so there is no longer any need for extra environment configuration. Double-check that `SITE_URL` is not polluted with Windows-style backslashes or drive prefixes so the derived URLs stay valid.
