// ────────────────────────────────────────────────────
// Patient Access Client - SMART on FHIR patient EHR-records flow.
// Browser-side: invitation -> JHE token -> Epic PKCE -> pull USCDI records -> write to JHE.
// Uses common.js (parseInvitationCode) and SMART fhir-client.js (FHIR.oauth2.*).
// ────────────────────────────────────────────────────

var TOKEN_ENDPOINT = window.location.origin + "/o/token/";
var API_ENDPOINT = window.location.origin + "/api/v1/";
// Epic serves R4; JHE validates R5. Writes go through the R4 import endpoint, which converts
// R4->R5 (cross_version engine) then runs the normal create. It returns a batch-response Bundle.
var IMPORT_ENDPOINT = window.location.origin + "/fhir-import/R4/";
var JHE_TOKEN_KEY = "patient_access_jhe_access_token";

function paStoreToken(token) {
  sessionStorage.setItem(JHE_TOKEN_KEY, token);
}

function paGetToken() {
  return sessionStorage.getItem(JHE_TOKEN_KEY);
}

// Exchange a JHE invitation auth code for a JHE access token (PKCE verifier = the token).
async function paExchangeCodeForToken(clientId, code, codeVerifier, redirectUri) {
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
async function paRedeemInvitation(out) {
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
  var tokens = await paExchangeCodeForToken(grant.client_id, grant.code, codeVerifier, grant.redirect_uri);
  if (!tokens || !tokens.access_token) {
    out.textContent += "\nError: failed to exchange invitation for JHE token";
    return false;
  }
  paStoreToken(tokens.access_token);
  out.textContent += "\nJHE access token received";
  return true;
}

// Attach the Epic patient id to the JHE patient (additive). Returns true on success.
async function paSavePatientIdentifier(jheToken, system, value) {
  var response = await fetch(API_ENDPOINT + "patient-access/identifier", {
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
// A FhirSource requires a DataSource (the seeded "Patient Access API" device), passed
// in from the page config.
async function paCreateFhirSource(jheToken, fhirBaseUrl, dataSourceId) {
  var response = await fetch(API_ENDPOINT + "fhir_sources", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + jheToken,
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
    },
    body: JSON.stringify({ label: "Epic / Patient Access", fhir_base_url: fhirBaseUrl, data_source: dataSourceId }),
  });
  if (!response.ok) return null;
  var data = await response.json();
  return data.id;
}

// Epic (with "Unconstrained FHIR IDs") can emit resource ids longer than the FHIR
// spec's 64-char limit, which the JHE FHIR write rejects. When that happens, move the
// over-long id into an identifier (no length limit, so provenance is kept) and drop the
// top-level id; JHE assigns the aux resource its own id. Mutates and returns the resource.
function paSanitizeResource(resource, iss) {
  if (resource && typeof resource.id === "string" && resource.id.length > 64) {
    resource.identifier = (resource.identifier || []).concat({ system: iss, value: resource.id });
    delete resource.id;
  }
  return resource;
}

// POST one R4 resource to the JHE R4 import endpoint (converts R4->R5, then creates).
// The endpoint returns HTTP 200 with a batch-response Bundle even when the single entry
// failed, so success is the entry's own create status (2xx), not just response.ok.
async function paWriteResource(jheToken, sourceId, resourceType, resource) {
  var response = await fetch(IMPORT_ENDPOINT + resourceType, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + jheToken,
      "Content-Type": "application/json",
      "X-JHE-FHIR-Source-ID": String(sourceId),
      "Cache-Control": "no-cache",
    },
    body: JSON.stringify(resource),
  });
  if (!response.ok) return false;
  var bundle = await response.json();
  var entry = bundle && bundle.entry && bundle.entry[0];
  var status = entry && entry.response && entry.response.status;
  return typeof status === "string" && status.charAt(0) === "2";
}

// USCDI resources pulled for the demo phenotype. `single` reads one instance (Patient),
// the rest are patient-scoped searches. Order is display order.
var PATIENT_ACCESS_PULLS = [
  { label: "Demographics", type: "Patient", query: "Patient", single: true },
  { label: "Conditions", type: "Condition", query: "Condition" },
  { label: "Medications", type: "MedicationRequest", query: "MedicationRequest" },
  { label: "Allergies", type: "AllergyIntolerance", query: "AllergyIntolerance" },
  { label: "Labs", type: "Observation", query: "Observation?category=laboratory" },
];

// Pull one resource type and write each item to JHE. Isolated so one type's failure
// (fetch error, unsupported type) does not abort the others. Returns {written, failed, error}.
async function paPullResourceType(client, jheToken, sourceId, pull, iss) {
  var resources;
  try {
    // A single instance read (Patient) is a plain read; fhir-client's patient.request injects a
    // ?patient= filter that Epic rejects for an instance read, so use client.request for it.
    // Searches stay on patient.request so they are scoped to this patient.
    var result = pull.single
      ? await client.request(pull.query + "/" + client.patient.id)
      : await client.patient.request(pull.query, { pageLimit: 0, flat: true });
    resources = pull.single ? (result ? [result] : []) : result || [];
  } catch (e) {
    return { written: 0, failed: 0, error: e && e.message ? e.message : String(e) };
  }
  var written = 0;
  var failed = 0;
  for (var i = 0; i < resources.length; i++) {
    var resource = resources[i];
    if (!resource || resource.resourceType !== pull.type) continue;
    paSanitizeResource(resource, iss);
    var ok = await paWriteResource(jheToken, sourceId, pull.type, resource);
    if (ok) written++;
    else failed++;
  }
  return { written: written, failed: failed, error: null };
}

// Search hospital brands for the picker. Returns an array of facility rows (or []).
async function paSearchBrands(jheToken, query) {
  var url = API_ENDPOINT + "patient-access/brands?q=" + encodeURIComponent(query || "");
  var response = await fetch(url, {
    headers: { Authorization: "Bearer " + jheToken, "Cache-Control": "no-cache" },
  });
  if (!response.ok) return [];
  var data = await response.json();
  return data.results || [];
}

// Launch the Epic SMART authorize against the selected hospital's FHIR base URL (iss).
// fhir-client.js discovers the authorize/token endpoints from iss via
// {iss}/.well-known/smart-configuration, so no per-hospital endpoint config is needed.
function paAuthorizeWithIss(config, iss) {
  FHIR.oauth2.authorize({
    iss: iss,
    clientId: config.clientId,
    scope: config.scope,
    redirectUri: window.location.origin + "/clients/patient-access/callback",
    pkceMode: "ifSupported",
  });
}

// Render hospital search results as clickable rows (name + address). Clicking a row
// calls onSelect(row). Returns the number of rows rendered (0 => shows a message).
function paRenderBrandResults(container, results, onSelect) {
  container.innerHTML = "";
  if (!results || results.length === 0) {
    var empty = document.createElement("div");
    empty.className = "text-muted p-2";
    empty.textContent = "No hospitals found. Try a different name, city, or state.";
    container.appendChild(empty);
    return 0;
  }
  var list = document.createElement("div");
  list.className = "list-group text-start";
  results.forEach(function (row) {
    var item = document.createElement("button");
    item.type = "button";
    item.className = "list-group-item list-group-item-action";
    item.setAttribute("data-brand-result", "");
    var title = document.createElement("div");
    title.className = "fw-bold";
    var facility = row.facilityName && row.facilityName !== row.brandName ? " — " + row.facilityName : "";
    title.textContent = row.brandName + facility;
    var addr = document.createElement("div");
    addr.className = "small text-muted";
    addr.textContent = row.addressText || "";
    item.appendChild(title);
    item.appendChild(addr);
    item.addEventListener("click", function () {
      onSelect(row);
    });
    list.appendChild(item);
  });
  container.appendChild(list);
  return results.length;
}

// Connect page entry point: redeem the invitation, then show the hospital picker.
// Selecting a hospital launches the Epic SMART authorize against that hospital's iss.
// `picker` = { input, results } DOM elements from the connect page.
async function startPatientAccessConnect(out, config, picker) {
  out.textContent = "Processing your invitation...";
  var ok = await paRedeemInvitation(out);
  if (!ok) return;
  out.textContent += "\n\nChoose your hospital to continue.";

  var jheToken = paGetToken();
  var onSelect = function (row) {
    out.textContent += "\n\nRedirecting to " + row.brandName + " login...";
    paAuthorizeWithIss(config, row.fhirBaseUrl);
  };

  var runSearch = async function () {
    var results = await paSearchBrands(jheToken, picker.input.value);
    paRenderBrandResults(picker.results, results, onSelect);
  };

  // Debounced live search as the patient types; initial call lists everything.
  var timer = null;
  picker.input.addEventListener("input", function () {
    if (timer) clearTimeout(timer);
    timer = setTimeout(runSearch, 200);
  });
  picker.container.hidden = false;
  await runSearch();
}

// Callback page entry point: finish Epic handshake, store id, pull USCDI records, write to JHE.
async function finishPatientAccessConnect(out, config) {
  out.textContent = "Completing connection...";
  var jheToken = paGetToken();
  if (!jheToken) {
    out.textContent += "\nError: no JHE session. Restart from your invitation link.";
    return;
  }

  var client;
  try {
    client = await FHIR.oauth2.ready();
  } catch (e) {
    out.textContent += "\nError: Patient Access authorization failed: " + (e && e.message ? e.message : e);
    return;
  }

  // The token must carry patient context (the launch/patient scope). Without it
  // we cannot attribute or scope the data, so stop with a clear message.
  var epicPatientId = client.patient && client.patient.id;
  if (!epicPatientId) {
    out.textContent += "\nError: no patient context from Patient Access (missing launch/patient scope)";
    return;
  }
  out.textContent += "\nEHR patient id: " + epicPatientId;

  // Provenance must be the hospital the patient actually picked and authorized against,
  // which fhir-client records as state.serverUrl - not any single configured default.
  var iss = client.state && client.state.serverUrl;
  if (!iss) {
    out.textContent += "\nError: no FHIR server URL from Patient Access authorization";
    return;
  }

  var idOk = await paSavePatientIdentifier(jheToken, iss, epicPatientId);
  if (!idOk) {
    out.textContent += "\nError: failed to store Patient Access patient id";
    return;
  }
  out.textContent += "\nStored Patient Access patient id in JHE";

  var sourceId = await paCreateFhirSource(jheToken, iss, config.dataSourceId);
  if (!sourceId) {
    out.textContent += "\nError: failed to register data source";
    return;
  }

  // Pull each USCDI type independently. pageLimit:0 + flat:true makes fhir-client.js
  // follow every `next` link so patients with more records than one page are not truncated.
  var summary = [];
  for (var p = 0; p < PATIENT_ACCESS_PULLS.length; p++) {
    var pull = PATIENT_ACCESS_PULLS[p];
    out.textContent += "\n\nFetching " + pull.label + " from Patient Access...";
    var result = await paPullResourceType(client, jheToken, sourceId, pull, iss);
    if (result.error) {
      out.textContent += "\n  could not fetch " + pull.label + ": " + result.error;
      summary.push(pull.label + ": fetch failed");
      continue;
    }
    out.textContent += "\n  saved " + result.written + " record(s)";
    summary.push(pull.label + ": " + result.written + (result.failed ? " (" + result.failed + " failed)" : ""));
  }

  out.textContent += "\n\nThe following information was added to JupyterHealth:\n\n" + summary.join("\n");
}

// Exposed for unit tests; browser runs load this as a plain <script> and ignore it.
if (typeof window !== "undefined") {
  window.paPullResourceType = paPullResourceType;
  window.paWriteResource = paWriteResource;
  window.PATIENT_ACCESS_PULLS = PATIENT_ACCESS_PULLS;
  window.paSearchBrands = paSearchBrands;
  window.paAuthorizeWithIss = paAuthorizeWithIss;
  window.paRenderBrandResults = paRenderBrandResults;
  window.paSavePatientIdentifier = paSavePatientIdentifier;
  window.finishPatientAccessConnect = finishPatientAccessConnect;
}
