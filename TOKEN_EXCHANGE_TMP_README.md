# SMART on FHIR EHR-Launch Token Exchange (temporary README)

> Working notes for the `/o/token-exchange` provider-SSO flow. Merge the relevant
> parts into the main docs before this file is removed.

JHE lets an external SMART on FHIR app trade an **EHR-issued OIDC `id_token`** for a
**JHE user-bound access token**, over OAuth 2.0 Token Exchange (RFC 8693). The app
authenticates as a confidential OAuth client ("SoF EHR Launch"); JHE verifies the
`id_token` offline against the EHR's JWKS and issues an access token bound to the
matching JHE `Practitioner` and linked to that client.

No EHR `userinfo` or introspection endpoint is required — verification relies only on
capabilities every ONC g(10)-certified EHR must expose (OIDC `id_token` with `fhirUser` + published JWKS), so the same code path works across EHR vendors with no per-vendor
plumbing.

---

## Architecture

```
                 ┌─────────────────────────── EHR (Epic, Cerner, MedPlum, …) ───────────────────┐
                 │  SMART App Launch (openid fhirUser)                                            │
   Provider ───► │  → issues id_token  (iss = EHR,  aud = app's EHR client_id,  fhirUser = ...)   │
                 └───────────────────────────────────────────────────────────────────────────────┘
                                  │  id_token (+ the app's JHE client_id/secret)
                                  ▼
   ┌──────────────────────── External SMART App (confidential) ───────────────────────┐
   │  POST /o/token-exchange   (client_id + client_secret,  subject_token = id_token)   │
   └───────────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
   ┌────────────────────────────────── JHE /o/token-exchange ─────────────────────────┐
   │  1. Authenticate the confidential client  (RFC 6749 client auth)                  │
   │  2. Read id_token `iss`; require it ∈ auth.sof.trusted_issuers                     │
   │  3. Discover JWKS: {iss}/.well-known/smart-configuration → jwks_uri               │
   │  4. Verify id_token signature + exp/iat + aud == auth.sof.trusted_audience         │
   │  5. fhirUser  ("Practitioner/<id>")  → JheUser.identifier == <id>                  │
   │  6. Issue access token: user = Practitioner,  application = the client             │
   └───────────────────────────────────────────────────────────────────────────────────┘
                                  │  { access_token, token_type: Bearer, expires_in }
                                  ▼
                    App calls JHE FHIR API with  Authorization: Bearer <access_token>
```

### Two distinct client identities (do not conflate)

| | Where it lives | What it is | Checked against |
| --- | --- | --- | --- |
| **EHR client_id** | inside the `id_token` as `aud` | the app's registration **at the EHR** | `auth.sof.trusted_audience` |
| **JHE client_id + secret** | the token-exchange request (client auth) | the app's registration **in JHE** (the "SoF EHR Launch" Application) | DOT Application store |

The `id_token` proves *which provider* is launching; the JHE client credential proves
*which app* is calling JHE. Both must be satisfied.

---

## Configuration (JheSettings)

Configured at runtime via `JheSetting` rows (not env vars). Both are created by
`python manage.py seed`:

| Key | Type | Meaning | Seeded example |
| --- | --- | --- | --- |
| `auth.sof.trusted_issuers` | `json` (array of strings) | EHR OIDC issuers (`id_token.iss`) whose tokens are accepted; JWKS is discovered from each. | `["https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"]` |
| `auth.sof.trusted_audience` | `string` | The SMART app's `client_id` at the EHR (`id_token.aud`). | `77849e74-8e2a-4c2f-826c-bdbef6da3357` |

Update them via the JheSettings admin/API - values are cached for ~60s; a change takes effect within that window.

### The "SoF EHR Launch" confidential client

`manage.py seed` also creates a confidential OAuth `Application` named **"SoF EHR
Launch"** that external apps authenticate as. Dev-only default credentials, **rotate
for any real deployment**):

```
client_id     = sof-ehr-launch
client_secret = sof-ehr-launch-dev-secret
```

Its `authorization_grant_type` is recorded as `client-credentials` because the app
authenticates directly with id+secret at the token endpoint (no browser redirect); the
issued token is nonetheless **user-bound** (see "Token issuance" below).

---

## External app: step-by-step integration

**One-time setup (coordinate with the JHE operator):**

1. **Get a JHE confidential client.** Obtain the "SoF EHR Launch" `client_id` /
   `client_secret` (or have a dedicated confidential Application created for you). Keep
   the secret server-side.
2. **Register your app at the EHR** as a SMART app with the `openid fhirUser` scopes.
   Note the `client_id` the EHR assigns you and the EHR's `iss` URL.
3. **Ask the operator to trust you:** add your EHR `iss` to `auth.sof.trusted_issuers`
   and set `auth.sof.trusted_audience` to your EHR `client_id`.
4. **Ensure each provider exists in JHE** as a `Practitioner` whose `JheUser.identifier`
   equals the **bare FHIR id** the EHR puts in `fhirUser` (e.g. for
   `fhirUser: "Practitioner/abc123"`, `identifier == "abc123"`). Unmatched providers get
   `404`.

**Per launch (runtime):**

5. **Complete the SMART App Launch** at the EHR with `scope=openid fhirUser …`. The EHR's
   token response includes an `id_token`.
6. **Call JHE token exchange** with your JHE client credentials and that `id_token` as the
   `subject_token` (see request below).
7. **Use the returned `access_token`** as `Authorization: Bearer <token>` against JHE's
   FHIR/API endpoints.

---

## The request

`POST {JHE_BASE_URL}/o/token-exchange` — `application/x-www-form-urlencoded`, **POST only**.

| Parameter | Required | Value |
| --- | --- | --- |
| `grant_type` | ✓ | `urn:ietf:params:oauth:grant-type:token-exchange` |
| `subject_token` | ✓ | the EHR-issued `id_token` (a JWT) |
| `subject_token_type` | ✓ | `urn:ietf:params:oauth:token-type:id_token` |
| `requested_token_type` | ✓ | `urn:ietf:params:oauth:token-type:access_token` |
| `audience` | ✓ | JHE's site URL (must equal JHE's `SITE_URL`) |
| `scope` | ✓ | `openid` (only value supported) |
| `client_id` | ✓ | your JHE confidential client id |
| `client_secret` | ✓ | your JHE confidential client secret |

Client credentials may instead be sent via HTTP Basic (`Authorization: Basic …`) — both
placements are accepted.

### Example (client_secret in the body)

```bash
curl -X POST 'https://jhe.example/o/token-exchange' \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=urn:ietf:params:oauth:grant-type:token-exchange' \
  --data-urlencode 'subject_token='"$EHR_ID_TOKEN" \
  --data-urlencode 'subject_token_type=urn:ietf:params:oauth:token-type:id_token' \
  --data-urlencode 'requested_token_type=urn:ietf:params:oauth:token-type:access_token' \
  --data-urlencode 'audience=https://jhe.example' \
  --data-urlencode 'scope=openid' \
  --data-urlencode 'client_id=sof-ehr-launch' \
  --data-urlencode 'client_secret=sof-ehr-launch-dev-secret'
```

### Example (client credentials via HTTP Basic)

```bash
curl -X POST 'https://jhe.example/o/token-exchange' \
  -u 'sof-ehr-launch:sof-ehr-launch-dev-secret' \
  --data-urlencode 'grant_type=urn:ietf:params:oauth:grant-type:token-exchange' \
  --data-urlencode 'subject_token='"$EHR_ID_TOKEN" \
  --data-urlencode 'subject_token_type=urn:ietf:params:oauth:token-type:id_token' \
  --data-urlencode 'requested_token_type=urn:ietf:params:oauth:token-type:access_token' \
  --data-urlencode 'audience=https://jhe.example' \
  --data-urlencode 'scope=openid'
```

### Success response `200`

```json
{
  "access_token": "9f8c…",
  "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
  "token_type": "Bearer",
  "expires_in": 1209600,
  "scope": "openid"
}
```

Then:

```bash
curl 'https://jhe.example/fhir/r5/Patient' \
  --header 'Authorization: Bearer 9f8c…'
```

### Error responses

| Status | Cause |
| --- | --- |
| `400` | Missing/invalid parameter, malformed JWT, wrong `subject_token_type`, `audience` ≠ JHE site URL, missing `fhirUser` |
| `401` | Client authentication failed; or `id_token` signature/`exp`/`aud` invalid |
| `403` | `id_token.iss` not in `auth.sof.trusted_issuers`; or `fhirUser` is not a `Practitioner`; or the JHE user is not a practitioner |
| `404` | No `Practitioner` with `JheUser.identifier` == the `fhirUser` id |
| `500` | Token exchange not configured (`auth.sof.*` unset) |
| `502` | JWKS discovery / signing-key resolution failed at the issuer |

---

## Token issuance (implementation note)

The endpoint authenticates the confidential client, then issues the access token via
`JheOAuth2Validator.save_bearer_token`, reusing the authenticated oauthlib request so the
resulting `AccessToken` row carries **both** `user` (the Practitioner) and `application`
(the client). This is the same "many users per client, user-bound bearer token" shape as
the `authorization_code` grant — issued directly here because DOT has no built-in
token-exchange grant handler. No refresh token is issued; the app re-exchanges per launch.

---

## Security notes / known limitations

- **Offline verification, no revocation.** A valid `id_token` is honored until its `exp`;
  there is no live check against the EHR, so an EHR-side session revocation is not
  reflected until the token expires. Prefer short EHR `id_token` lifetimes.
- **No replay / one-time-use tracking.** A captured `id_token` can be re-exchanged within
  its validity window. Client authentication (confidential client) is the primary control;
  a leaked `id_token` alone is not sufficient without the client secret.
- **Identity mapping is issuer-unscoped.** `fhirUser` is matched on the bare FHIR id
  against `JheUser.identifier`; this assumes **one trusted EHR per JHE instance**. Do not
  configure issuers from multiple EHRs whose Practitioner ids could collide.
- **`JheUser.identifier` is not unique** at the DB level; a duplicate returns `404` (not a
  500) but indicates a provisioning problem to fix.
- Dev-seeded client credentials are placeholders — rotate before any non-local use.
