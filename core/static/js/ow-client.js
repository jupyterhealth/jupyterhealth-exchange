// ────────────────────────────────────────────────────
// OW Client - helper functions for the Open Wearables
// patient integration flow. Uses common.js utilities.
// ────────────────────────────────────────────────────

var TOKEN_ENDPOINT = window.location.origin + "/o/token/";
var API_ENDPOINT = window.location.origin + "/api/v1/";

// Exchange an authorization code for an access token.
// Returns the parsed token response JSON on success, or null on failure.
async function exchangeCodeForToken(clientId, code, codeVerifier, redirectUri) {
  var payload = {
    code: code,
    grant_type: "authorization_code",
    redirect_uri: redirectUri,
    client_id: clientId,
    code_verifier: codeVerifier,
  };

  var response = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "Cache-Control": "no-cache",
    },
    body: new URLSearchParams(payload).toString(),
  });

  if (!response.ok) {
    return null;
  }
  return await response.json();
}

// Fetch the patient's profile (including patient.id).
async function getPatientProfile(accessToken) {
  var response = await fetch(API_ENDPOINT + "users/profile", {
    headers: {
      Authorization: "Bearer " + accessToken,
      "Cache-Control": "no-cache",
    },
  });
  if (!response.ok) {
    return null;
  }
  return await response.json();
}

// Fetch the patient's consents (pending + already-consented).
async function getConsents(accessToken, patientId) {
  var response = await fetch(API_ENDPOINT + "patients/" + patientId + "/consents", {
    headers: {
      Authorization: "Bearer " + accessToken,
      "Cache-Control": "no-cache",
    },
  });
  if (!response.ok) {
    return null;
  }
  return await response.json();
}

// POST consent decisions. Use method="POST" for first-time consents on
// pending scopes, and method="PATCH" to update existing consent rows.
async function submitConsents(accessToken, patientId, studyScopeConsents, method) {
  if (!studyScopeConsents.length) return true;
  var response = await fetch(API_ENDPOINT + "patients/" + patientId + "/consents", {
    method: method || "POST",
    headers: {
      Authorization: "Bearer " + accessToken,
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
    },
    body: JSON.stringify({ study_scope_consents: studyScopeConsents }),
  });
  return response.ok;
}

// Render the consent form into `container`. Lists every study (pending
// AND already-consented) with one checkbox per requested scope. Default
// state matches the patient's current JHE consent record (not Oura's
// OAuth grants - JHE is the single source of truth that gates ow_poll).
//
// Calls `onSubmit(studyScopeConsents)` when the user clicks "Save".
function renderConsentForm(container, consentsData, onSubmit) {
  container.innerHTML = "";

  var pending = consentsData.studiesPendingConsent || consentsData.studies_pending_consent || [];
  var responded = consentsData.studies || [];

  // Build a unified list. Each entry: { study, scopes: [{coding_system, coding_code, text, consented}] }.
  var rows = [];
  function mapScopes(list) {
    return (list || []).map(function (r) {
      var code = r.code || {};
      var system = code.codingSystem || code.coding_system;
      var codeStr = code.codingCode || code.coding_code;
      return {
        coding_system: system,
        coding_code: codeStr,
        text: code.text || codeStr,
        consented: r.consented === true,
      };
    });
  }
  pending.forEach(function (s) {
    rows.push({ study: s, isPending: true, scopes: mapScopes(s.pendingScopeConsents || s.pending_scope_consents) });
  });
  responded.forEach(function (s) {
    rows.push({ study: s, isPending: false, scopes: mapScopes(s.scopeConsents || s.scope_consents) });
  });

  if (rows.length === 0) {
    container.innerHTML = '<p class="text-muted">No studies require your consent.</p>';
    return;
  }

  var form = document.createElement("form");
  form.className = "text-start";

  rows.forEach(function (row, i) {
    var section = document.createElement("div");
    section.className = "mb-3 p-3 border rounded";
    var title = document.createElement("h5");
    title.textContent = row.study.name || "Study";
    section.appendChild(title);

    row.scopes.forEach(function (scope, j) {
      var id = "consent_" + i + "_" + j;
      var wrap = document.createElement("div");
      wrap.className = "form-check";
      var input = document.createElement("input");
      input.type = "checkbox";
      input.className = "form-check-input";
      input.id = id;
      input.checked = scope.consented;
      input.dataset.studyId = row.study.id;
      input.dataset.isPending = row.isPending ? "1" : "0";
      input.dataset.codingSystem = scope.coding_system;
      input.dataset.codingCode = scope.coding_code;
      var label = document.createElement("label");
      label.className = "form-check-label";
      label.htmlFor = id;
      label.textContent = scope.text;
      wrap.appendChild(input);
      wrap.appendChild(label);
      section.appendChild(wrap);
    });

    form.appendChild(section);
  });

  var btn = document.createElement("button");
  btn.type = "submit";
  btn.className = "btn btn-primary";
  btn.textContent = "Save";
  form.appendChild(btn);

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    // Pending scopes (no row yet) need POST; existing rows need PATCH.
    var newByStudy = {};
    var updateByStudy = {};
    form.querySelectorAll('input[type="checkbox"]').forEach(function (input) {
      var sid = input.dataset.studyId;
      var bucket = input.dataset.isPending === "1" ? newByStudy : updateByStudy;
      if (!bucket[sid]) bucket[sid] = { study_id: parseInt(sid, 10), scope_consents: [] };
      bucket[sid].scope_consents.push({
        coding_system: input.dataset.codingSystem,
        coding_code: input.dataset.codingCode,
        consented: input.checked,
      });
    });
    onSubmit({
      created: Object.values(newByStudy),
      updated: Object.values(updateByStudy),
    });
  });

  container.appendChild(form);
}

// Create an OW user via JHE proxy endpoint.
// Returns the response JSON (contains ow_user_id), or null on failure.
async function createOwUser(accessToken) {
  var response = await fetch(API_ENDPOINT + "ow/users", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + accessToken,
      "Cache-Control": "no-cache",
    },
  });
  if (!response.ok) {
    return null;
  }
  return await response.json();
}

// Get the Oura OAuth authorization URL via JHE proxy endpoint.
// Returns the response JSON (contains authorizationUrl), or null on failure.
async function getOuraAuthUrl(accessToken, redirectUri) {
  var params = new URLSearchParams({ redirect_uri: redirectUri });
  var response = await fetch(
    API_ENDPOINT + "ow/oauth/oura/authorize?" + params.toString(),
    {
      headers: {
        Authorization: "Bearer " + accessToken,
        "Cache-Control": "no-cache",
      },
    }
  );
  if (!response.ok) {
    return null;
  }
  return await response.json();
}

// Get patient's wearable connection status from OW.
// Returns { connections: [...], connected: bool }, or null on failure.
async function getWearableStatus(accessToken, patientId) {
  var response = await fetch(API_ENDPOINT + "patients/" + patientId + "/wearable-status", {
    headers: {
      Authorization: "Bearer " + accessToken,
      "Cache-Control": "no-cache",
    },
  });
  if (!response.ok) {
    return null;
  }
  return await response.json();
}

// Get authenticated patient's own data via /patients/me.
async function getPatientMe(accessToken) {
  var response = await fetch(API_ENDPOINT + "patients/me/", {
    headers: {
      Authorization: "Bearer " + accessToken,
      "Cache-Control": "no-cache",
    },
  });
  if (!response.ok) {
    return null;
  }
  return await response.json();
}

// Token storage helpers (sessionStorage - tab-scoped, cleared on tab close).
function storeToken(token) {
  try {
    sessionStorage.setItem("ow_access_token", token);
  } catch (e) {
    // sessionStorage unavailable (e.g. incognito with storage disabled)
  }
}

function getStoredToken() {
  try {
    return sessionStorage.getItem("ow_access_token");
  } catch (e) {
    return null;
  }
}
