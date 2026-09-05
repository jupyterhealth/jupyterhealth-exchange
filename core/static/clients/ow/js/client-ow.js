// ────────────────────────────────────────────────────
// OW Client - registers the connect step for the shared
// patient-facing app (core/static/common/js/patient-facing.js).
// ────────────────────────────────────────────────────

// Create an OW user via JHE proxy endpoint; returns the response JSON (has ow_user_id), or null on failure.
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

// Get the Oura OAuth authorization URL via JHE proxy endpoint; returns the response JSON (has authorizationUrl), or null on failure.
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

// Get patient's wearable connection status from OW; returns { connections: [...], connected: bool }, or null on failure.
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

// Connect step: the branded card for the consented source; Continue starts the Oura authorization.
pfClient.connect = async function (source) {
  pfRender("t-launch", {
    sourceName: source.name,
    sourceLabels: source.consentedLabels.join(", "),
    siteTitle: PATIENT_PORTAL_CONFIG.siteTitle,
  });
};

// Create the OW user, fetch the Oura authorize URL and redirect to it; any failure shows the error callout.
async function owContinue() {
  var accessToken = getStoredToken();
  pfShowLoading();
  try {
    var owUser = await createOwUser(accessToken);
    if (!owUser) throw new Error("failed to create OW user");
    var ouraAuth = await getOuraAuthUrl(accessToken, window.location.origin + "/clients/ow/complete");
    if (!ouraAuth || !ouraAuth.authorizationUrl) throw new Error("failed to get Oura auth URL");
  } catch (e) {
    pfHideLoading();
    showFlowError("We couldn't connect your wearable", e.message);
    return;
  }
  window.location.href = ouraAuth.authorizationUrl;
}

if (typeof window !== "undefined") {
  window.owContinue = owContinue;
}
