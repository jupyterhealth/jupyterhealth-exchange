// ────────────────────────────────────────────────────
// MyChart Client - SMART on FHIR patient EHR-records flow.
// Browser-side: invitation -> JHE token -> Epic PKCE -> pull Labs -> write to JHE.
// Uses common.js (parseInvitationCode) and SMART fhir-client.js (FHIR.oauth2.*).
// ────────────────────────────────────────────────────

var TOKEN_ENDPOINT = window.location.origin + "/o/token/";
var API_ENDPOINT = window.location.origin + "/api/v1/";
var FHIR_ENDPOINT = window.location.origin + "/FHIR/R5/";
var JHE_TOKEN_KEY = "mychart_jhe_access_token";

function mcStoreToken(token) {
  sessionStorage.setItem(JHE_TOKEN_KEY, token);
}

function mcGetToken() {
  return sessionStorage.getItem(JHE_TOKEN_KEY);
}

// Exchange a JHE invitation auth code for a JHE access token (PKCE verifier = the token).
async function mcExchangeCodeForToken(clientId, code, codeVerifier, redirectUri) {
  var payload = {
    code: code,
    grant_type: "authorization_code",
    redirect_uri: redirectUri,
    client_id: clientId,
    code_verifier: codeVerifier,
  };
  var response = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache" },
    body: new URLSearchParams(payload).toString(),
  });
  if (!response.ok) return null;
  return await response.json();
}

// Redeem the ?code= invitation and obtain + store a JHE access token. Returns true on success.
async function mcRedeemInvitation(out) {
  var params = new URLSearchParams(window.location.search);
  var code = params.get("code");
  if (!code) {
    out.textContent += "\nError: no invitation code in URL";
    return false;
  }
  var link = parseInvitationCode(code);
  if (!link) {
    out.textContent += "\nError: invalid invitation code format";
    return false;
  }
  out.textContent += "\nRedeeming invitation...";
  var invitationResponse = await fetch(
    window.location.protocol + "//" + link.host + "/api/v1/invitation/" + link.token,
    { method: "POST", headers: { "Cache-Control": "no-cache" } }
  );
  if (!invitationResponse.ok) {
    out.textContent += "\nError: failed to redeem invitation (" + invitationResponse.status + ")";
    return false;
  }
  var grant = (await invitationResponse.json()).grant;
  var codeVerifier = btoa(link.token).replace(/=/g, "");
  var tokens = await mcExchangeCodeForToken(grant.client_id, grant.code, codeVerifier, grant.redirect_uri);
  if (!tokens || !tokens.access_token) {
    out.textContent += "\nError: failed to exchange invitation for JHE token";
    return false;
  }
  mcStoreToken(tokens.access_token);
  out.textContent += "\nJHE access token received";
  return true;
}

// Attach the Epic patient id to the JHE patient (additive). Returns true on success.
async function mcSavePatientIdentifier(jheToken, system, value) {
  var response = await fetch(API_ENDPOINT + "mychart/identifier", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + jheToken,
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
    },
    body: JSON.stringify({ system: system, value: value }),
  });
  return response.ok;
}

// Register a FhirSource for this Epic connection. Returns the source id or null.
// A FhirSource requires a DataSource (the seeded "Epic MyChart" device), passed
// in from the page config.
async function mcCreateFhirSource(jheToken, fhirBaseUrl, dataSourceId) {
  var response = await fetch(API_ENDPOINT + "fhir_sources", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + jheToken,
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
    },
    body: JSON.stringify({ label: "Epic / MyChart", fhir_base_url: fhirBaseUrl, data_source: dataSourceId }),
  });
  if (!response.ok) return null;
  var data = await response.json();
  return data.id;
}

// Epic (with "Unconstrained FHIR IDs") can emit resource ids longer than the FHIR
// spec's 64-char limit, which the JHE FHIR write rejects. When that happens, move the
// over-long id into an identifier (no length limit, so provenance is kept) and drop the
// top-level id; JHE assigns the aux resource its own id. Mutates and returns the resource.
function mcSanitizeResource(resource, iss) {
  if (resource && typeof resource.id === "string" && resource.id.length > 64) {
    resource.identifier = (resource.identifier || []).concat({ system: iss, value: resource.id });
    delete resource.id;
  }
  return resource;
}

// POST one FHIR resource to the JHE FHIR endpoint as an aux resource. Returns true on success.
async function mcWriteResource(jheToken, sourceId, resourceType, resource) {
  var response = await fetch(FHIR_ENDPOINT + resourceType, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + jheToken,
      "Content-Type": "application/json",
      "X-JHE-FHIR-Source-ID": String(sourceId),
      "Cache-Control": "no-cache",
    },
    body: JSON.stringify(resource),
  });
  return response.ok;
}

// Connect page entry point: redeem invitation, then launch Epic SMART authorize.
async function startMyChartConnect(out, config) {
  out.textContent = "Processing your invitation...";
  var ok = await mcRedeemInvitation(out);
  if (!ok) return;
  out.textContent += "\n\nRedirecting to MyChart login...";
  FHIR.oauth2.authorize({
    iss: config.iss,
    clientId: config.clientId,
    scope: config.scope,
    redirectUri: window.location.origin + "/clients/mychart/callback",
    pkceMode: "ifSupported",
  });
}

// Callback page entry point: finish Epic handshake, store id, pull Labs, write to JHE.
async function finishMyChartConnect(out, config) {
  out.textContent = "Completing connection...";
  var jheToken = mcGetToken();
  if (!jheToken) {
    out.textContent += "\nError: no JHE session. Restart from your invitation link.";
    return;
  }

  var client;
  try {
    client = await FHIR.oauth2.ready();
  } catch (e) {
    out.textContent += "\nError: MyChart authorization failed: " + (e && e.message ? e.message : e);
    return;
  }

  // The token must carry patient context (the launch/patient scope). Without it
  // we cannot attribute or scope the data, so stop with a clear message.
  var epicPatientId = client.patient && client.patient.id;
  if (!epicPatientId) {
    out.textContent += "\nError: no patient context from MyChart (missing launch/patient scope)";
    return;
  }
  out.textContent += "\nMyChart patient id: " + epicPatientId;

  var idOk = await mcSavePatientIdentifier(jheToken, config.iss, epicPatientId);
  if (!idOk) {
    out.textContent += "\nError: failed to store MyChart patient id";
    return;
  }
  out.textContent += "\nStored MyChart patient id in JHE";

  var sourceId = await mcCreateFhirSource(jheToken, config.iss, config.dataSourceId);
  if (!sourceId) {
    out.textContent += "\nError: failed to register data source";
    return;
  }

  out.textContent += "\n\nFetching Labs from MyChart...";
  // pageLimit: 0 + flat: true makes fhir-client.js follow every `next` link and
  // return a flat array of Observation resources, so a patient with more Labs
  // than fit on one page is not truncated.
  var resources;
  try {
    resources = await client.patient.request("Observation?category=laboratory", { pageLimit: 0, flat: true });
  } catch (e) {
    out.textContent += "\nError: failed to fetch Labs: " + (e && e.message ? e.message : e);
    return;
  }
  resources = resources || [];
  out.textContent += "\nFetched " + resources.length + " Lab records";

  var written = 0;
  var failed = 0;
  for (var i = 0; i < resources.length; i++) {
    var resource = resources[i];
    if (!resource || resource.resourceType !== "Observation") continue;
    mcSanitizeResource(resource, config.iss);
    var ok = await mcWriteResource(jheToken, sourceId, "Observation", resource);
    if (ok) written++;
    else failed++;
  }

  out.textContent += "\n\nThe following information was successfully added to JupyterHealth:";
  out.textContent += "\n\nLabs: " + written + " records";
  if (failed) out.textContent += "\n(" + failed + " records could not be saved)";
}
