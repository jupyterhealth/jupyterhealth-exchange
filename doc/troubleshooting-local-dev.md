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
To resolve this issue, explicitly hardcode the correct values into your `settings.py` file, as shown below:

```python
OIDC_CLIENT_REDIRECT_URI = 'http://localhost:8000/auth/callback'
OIDC_CLIENT_AUTHORITY = 'http://localhost:8000/o/'
```

By explicitly defining these variables, you prevent incorrect path injections and resolve the blank screen issue on Windows machines.
