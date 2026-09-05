# patient-access-client — email one-time-code OAuth client demo

A dependency-free, static OAuth **authorization-code + PKCE** client that exercises
the email one-time-code login (see [../../login_otp.md](../../login_otp.md)).

- `index.html` — a **Log in** button that starts the PKCE flow. Because the
  client's `client_id` is listed in `auth.patient_access_clients`, JHE shows the
  email-code screen instead of the password form.
- `callback.html` — reads the returned auth `code`, verifies `state`, and POSTs to
  the token endpoint (inline JS) to exchange it for tokens, displaying the result.

## Prerequisites (one-time)

Run all snippets from the JHE repo root: `uv run python manage.py shell`.

### 1. Register a public OAuth client

```python
from oauth2_provider.models import get_application_model

App = get_application_model()
app, created = App.objects.get_or_create(
    name="OTP Demo Client",
    defaults=dict(
        client_type="public",  # browser client, no secret
        authorization_grant_type="authorization-code",
        redirect_uris="http://localhost:8001/callback.html",
        skip_authorization=True,  # skip the consent screen
    ),
)
print("client_id:", app.client_id)
```

Copy the printed `client_id`.

### 2. Route that client to the email-code flow

```python
from core.models import JheSetting

s, _ = JheSetting.objects.update_or_create(
    key="auth.patient_access_clients",
    defaults={"value_type": "json"},
)
s.set_value("json", [app.client_id])  # `app` from step 1
s.save()
```

(`get_setting` caches for 60s — wait a minute or restart the server.)

### 3. Make sure a patient account exists

The flow only logs in **existing** users. Use an existing patient's email, or
create one:

```python
from core.models import JheUser

JheUser.objects.create_user(
    email="patient@example.org",
    password="unused",
    user_type="patient",
    identifier="demo-patient",
)
```

### 4. Put the `client_id` into the demo

Edit `CONFIG.clientId` in **both** `index.html` and `callback.html`, replacing
`REPLACE_WITH_CLIENT_ID` with the value from step 1.

## Run it

1. Start JHE on :8000 (it now ships `core.middleware.OAuthCorsMiddleware`, which
   adds permissive CORS on `/o/` so the cross-origin token POST can be read):
   ```
   uv run python manage.py runserver 8000
   ```
2. Serve this directory on :8001:
   ```
   cd tests/patient-access-client && ./start_server.bash
   ```
   (`start_server.bash` just runs `python3 -m http.server 8001`.)
3. Open <http://localhost:8001/index.html> and click **Log in with email code**.
4. Enter the patient's email. In local dev the console email backend is used, so
   the **one-time code prints to the JHE server log**. Enter it.
5. You're redirected to `callback.html`, which exchanges the code and shows the
   token JSON.

## How it maps to the flow

```
index.html  --(/o/authorize/?client_id=…&code_challenge=…)-->  JHE
JHE  --(unauthenticated)-->  /accounts/login/?next=…  --(client_id listed)-->  /accounts/login-otp/
email -> code -> login  -->  back to /o/authorize/  -->  302 callback.html?code=…&state=…
callback.html  --(POST /o/token/ with code + code_verifier)-->  tokens
```

## Troubleshooting

- **CORS / "Failed to fetch" on the token POST** — confirm JHE is running with
  `core.middleware.OAuthCorsMiddleware` in `MIDDLEWARE` (it is, by default).
- **`invalid_grant` / redirect_uri mismatch** — `redirect_uri` must match the
  registered value and the `CONFIG.redirectUri` here *exactly*.
- **`server_error` requesting `openid`** — OIDC id-token signing needs
  `OIDC_RSA_PRIVATE_KEY` set in the JHE environment. If it isn't, drop `openid`
  from `CONFIG.scope` and use just `email`.
- **Password form instead of the code screen** — the `client_id` isn't in
  `auth.patient_access_clients` yet, or the 60s settings cache hasn't expired.

> This client allows any origin via permissive CORS on `/o/` — fine for a local
> demo, but tighten the allowed origin before using it anywhere real.
