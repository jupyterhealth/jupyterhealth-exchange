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

// Fetch the patient's pending consents and auto-accept all of them.
// Returns the consents response from the GET, or null on failure.
async function fetchAndAcceptConsents(accessToken) {
  // Step 1: Get the patient profile to find patient id
  var profileResponse = await fetch(API_ENDPOINT + "users/profile", {
    headers: {
      Authorization: "Bearer " + accessToken,
      "Cache-Control": "no-cache",
    },
  });
  if (!profileResponse.ok) {
    return null;
  }
  var profile = await profileResponse.json();
  var patientId = profile.patient.id;

  // Step 2: Get pending consents
  var consentsResponse = await fetch(
    API_ENDPOINT + "patients/" + patientId + "/consents",
    {
      headers: {
        Authorization: "Bearer " + accessToken,
        "Cache-Control": "no-cache",
      },
    }
  );
  if (!consentsResponse.ok) {
    return null;
  }
  var consentsData = await consentsResponse.json();

  // Step 3: Build consent payload from pending studies (accept all)
  var pendingStudies = consentsData.studies_pending_consent || [];
  if (pendingStudies.length === 0) {
    return consentsData;
  }

  var studyScopeConsents = [];
  for (var i = 0; i < pendingStudies.length; i++) {
    var study = pendingStudies[i];
    var scopeConsents = [];
    var scopes = study.scope_requests || [];
    for (var j = 0; j < scopes.length; j++) {
      scopeConsents.push({
        coding_system: scopes[j].scope_code.coding_system,
        coding_code: scopes[j].scope_code.coding_code,
        consented: true,
      });
    }
    studyScopeConsents.push({
      study_id: study.id,
      scope_consents: scopeConsents,
    });
  }

  // Step 4: POST consents
  var postResponse = await fetch(
    API_ENDPOINT + "patients/" + patientId + "/consents",
    {
      method: "POST",
      headers: {
        Authorization: "Bearer " + accessToken,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
      },
      body: JSON.stringify({ study_scope_consents: studyScopeConsents }),
    }
  );
  if (!postResponse.ok) {
    return null;
  }
  return consentsData;
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
// Returns the response JSON (contains authorization_url), or null on failure.
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
