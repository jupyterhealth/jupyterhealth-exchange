// ────────────────────────────────────────────────────
// Patient-facing app: the invitation journey (hub ->
// consent -> connect -> done / manage) as vanilla
// JavaScript + Handlebars components, the same shape
// as client-jhe-admin.js. A client page injects
// PATIENT_PORTAL_CONFIG, rolls up the components and
// calls patientApp(). The client script (client-ow.js
// or client-ehr-patient-portal.js) registers
// pfClient.connect(source) for its own connect step.
// ────────────────────────────────────────────────────

var TOKEN_ENDPOINT = window.location.origin + "/o/token/";
var API_ENDPOINT = window.location.origin + "/api/v1/";
var PF_TOKEN_KEY = "pf_access_token";
var PF_DEFAULT_ROUTE = "hub";
var PF_INVALID_INVITATION_TITLE = "This invitation link isn't valid";
var PF_INVALID_INVITATION_MESSAGE = "It may have expired or been replaced. Ask your study team for a new link.";

// The client script fills this in; the shared screens only ever call pfClient.connect(source).
var pfClient = { connect: null };

// ────────────────────────────────────────────────────
// Token
// ────────────────────────────────────────────────────

// Keep the bearer token in sessionStorage (tab-scoped, cleared on tab close).
function storeToken(token) {
  try {
    sessionStorage.setItem(PF_TOKEN_KEY, token);
  } catch (e) {
    // sessionStorage unavailable (e.g. incognito with storage disabled)
  }
}

function getStoredToken() {
  try {
    return sessionStorage.getItem(PF_TOKEN_KEY);
  } catch (e) {
    return null;
  }
}

// Exchange an authorization code for an access token; null on failure.
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
    headers: { "Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache" },
    body: new URLSearchParams(payload).toString(),
  });
  if (!response.ok) return null;
  return await response.json();
}

// Redeem the ?code= invitation (host_token) for a JHE access token and store it; throws patient-readable text.
async function pfRedeemInvitation(code) {
  var link = parseInvitationCode(code);
  if (!link) throw new Error(PF_INVALID_INVITATION_MESSAGE);
  var response = await fetch(window.location.protocol + "//" + link.host + "/api/v1/invitation/" + link.token, {
    method: "POST",
    headers: { "Cache-Control": "no-cache" },
  });
  if (!response.ok) throw new Error(PF_INVALID_INVITATION_MESSAGE);
  var grant = (await response.json()).grant;
  // The PKCE verifier is derived from the invitation token (see server-side issuance).
  var codeVerifier = btoa(link.token).replace(/=/g, "");
  var tokens = await exchangeCodeForToken(grant.client_id, grant.code, codeVerifier, grant.redirect_uri);
  if (!tokens || !tokens.access_token) throw new Error(PF_INVALID_INVITATION_MESSAGE);
  storeToken(tokens.access_token);
}

// ────────────────────────────────────────────────────
// Routing and rendering
// ────────────────────────────────────────────────────

// Route and params from the query string: "?route=consent&source=12" -> {route: "consent", params: {source: "12"}}.
function pfRouteAndParams(search) {
  var params = Object.fromEntries(new URLSearchParams(search === undefined ? window.location.search : search));
  var route = params.route || PF_DEFAULT_ROUTE;
  delete params.route;
  return { route: route, params: params };
}

// The current page's URL for a route and its params.
function pfUrl(route, params) {
  var query = new URLSearchParams(Object.assign({ route: route }, params || {})).toString();
  return window.location.pathname + "?" + query;
}

// Compile the component <script id="templateId"> and render it into #pf_main.
function pfRender(templateId, context) {
  var template = Handlebars.compile(document.getElementById(templateId).innerHTML);
  document.getElementById("pf_main").innerHTML = template(context || {});
  window.scrollTo(0, 0);
}

// Register the components other components include ({{> receipt}}, {{> rail}}) when the page carries them.
function pfRegisterPartials() {
  ["receipt", "rail"].forEach(function (name) {
    var el = document.getElementById("t-" + name);
    if (el) Handlebars.registerPartial(name, el.innerHTML);
  });
}

function pfShowLoading() {
  var overlay = document.getElementById("navLoadingOverlay");
  if (overlay) overlay.style.display = "flex";
}

function pfHideLoading() {
  var overlay = document.getElementById("navLoadingOverlay");
  if (overlay) overlay.style.display = "none";
}

// The patient-readable text of a failed API response.
async function pfErrorText(response) {
  try {
    var data = await response.json();
    if (typeof data === "string") return data;
    return data.error || data.detail || response.status + " " + response.statusText;
  } catch (e) {
    return response.status + " " + response.statusText;
  }
}

// Bearer-authenticated JSON request against /api/v1/; resolves to the parsed body (null for 204), throws on error.
async function pfApi(method, path, body) {
  var headers = { Authorization: "Bearer " + getStoredToken(), "Cache-Control": "no-cache" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  var response = await fetch(API_ENDPOINT + path, {
    method: method,
    headers: headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await pfErrorText(response));
  if (response.status === 204) return null;
  return await response.json();
}

// Replace the screen with the error callout; Try again re-runs the current route unless actions.retryHref is given.
function showFlowError(title, message, actions) {
  actions = actions || {};
  pfRender("t-error", {
    title: title,
    message: message,
    retryLabel: actions.retryLabel || "Try again",
    retryHref: actions.retryHref || null,
    backHref: PATIENT_PORTAL_CONFIG.pageUrl,
  });
}

// Re-run the app from the current URL (a ?code= still present is redeemed again).
function pfRetry() {
  return patientApp();
}

// Navigate to a route: push (or replace) history, render the screen, show the overlay while it loads.
async function pfNav(route, params, replace) {
  if (!Object.prototype.hasOwnProperty.call(PF_ROUTES, route)) route = PF_DEFAULT_ROUTE;
  params = params || {};
  var url = pfUrl(route, params);
  if (url !== window.location.pathname + window.location.search) {
    window.history[replace ? "replaceState" : "pushState"]({}, "", url);
  }
  document.title = PATIENT_PORTAL_CONFIG.siteTitle + " - " + PF_ROUTE_TITLES[route];
  if (route === "error" && params.title) document.title = PATIENT_PORTAL_CONFIG.siteTitle + " - " + params.title;
  pfShowLoading();
  try {
    await PF_ROUTES[route](params);
  } catch (e) {
    console.error(e);
    showFlowError("Something went wrong", e && e.message ? e.message : String(e));
  } finally {
    pfHideLoading();
  }
}

window.addEventListener("popstate", function () {
  var current = pfRouteAndParams();
  pfNav(current.route, current.params, true);
});

// Error route: ?route=error&title=...&message=... (how /clients/ow/complete reports an Oura failure).
async function renderError(params) {
  // Re-rendering this route would just show the same error, so Try again restarts the connect step.
  showFlowError(params.title || "Something went wrong", params.message || "", {
    retryHref: pfUrl("connect", { source: String(PATIENT_PORTAL_CONFIG.dataSourceIds[0]) }),
  });
}

// Entry point: redeem a ?code= if present, require a token, then render the route in the URL.
async function patientApp() {
  pfRegisterPartials();
  var current = pfRouteAndParams();
  if (current.params.code) {
    pfShowLoading();
    try {
      await pfRedeemInvitation(current.params.code);
    } catch (e) {
      pfHideLoading();
      showFlowError(PF_INVALID_INVITATION_TITLE, e && e.message ? e.message : PF_INVALID_INVITATION_MESSAGE);
      return;
    }
    pfHideLoading();
    delete current.params.code;
    window.history.replaceState({}, "", pfUrl(current.route, current.params));
  }
  if (!getStoredToken()) {
    showFlowError("Your session has ended", "Open the link from your invitation email to continue.");
    return;
  }
  await pfNav(current.route, current.params, true);
}

// ────────────────────────────────────────────────────
// Sources
// ────────────────────────────────────────────────────

// DataSource.type -> bootstrap-icons glyph on its card.
var PF_TYPE_ICONS = { patient_app: "bi-file-earmark-text", medical_device: "bi-activity", personal_device: "bi-smartwatch" };

var pfPatient = null;

// Scope text without a trailing coding-standard parenthetical: "Sleep episode (IEEE)" -> "Sleep episode".
function pfPatientLabel(text) {
  return (text || "").replace(/\s*\([^)]*\)\s*$/, "");
}

function pfUniqueSorted(list) {
  return list.filter(function (item, i) { return list.indexOf(item) === i; }).sort();
}

// FhirAuxResource.resource_type -> receipt row label; anything else is pluralized CamelCase words.
var PF_RESOURCE_LABELS = {
  Patient: "Demographics",
  MedicationRequest: "Medications",
  MedicationDispense: "Medication dispenses",
  AllergyIntolerance: "Allergies",
  DiagnosticReport: "Diagnostic reports",
  DocumentReference: "Documents",
  ServiceRequest: "Service requests",
  CarePlan: "Care plans",
  CareTeam: "Care teams",
  QuestionnaireResponse: "Questionnaire responses",
};

function pfResourceLabel(type) {
  return PF_RESOURCE_LABELS[type] || (type.match(/[A-Z][a-z0-9]*/g) || [type]).join(" ") + "s";
}

// Comma-joined expected-type labels for the consent subtext, "Demographics" first; "" when the client has none.
function pfScopeDetail(expectedTypes) {
  var labels = expectedTypes.map(pfResourceLabel).sort();
  if (!labels.length) return "";
  var i = labels.indexOf("Demographics");
  if (i > 0) labels.splice(0, 0, labels.splice(i, 1)[0]);
  return [labels[0]].concat(labels.slice(1).map(function (l) { return l.toLowerCase(); })).join(", ");
}

// One view model per data source of this client, from the consents payload (only config.dataSourceIds are listed).
function pfSources(consents, config) {
  var wanted = config.dataSourceIds.map(String);
  var byId = {};
  function collect(studies, pending) {
    (studies || []).forEach(function (study) {
      var rows = pending ? study.pendingScopeConsents : study.scopeConsents;
      (study.dataSources || []).forEach(function (ds) {
        if (wanted.indexOf(String(ds.id)) === -1) return;
        var supported = ds.supportedScopes.map(function (s) { return s.id; });
        rows.forEach(function (row) {
          if (supported.indexOf(row.code.id) === -1) return;
          var source = byId[ds.id] || (byId[ds.id] = {
            id: ds.id,
            name: config.sourceLabels[ds.id] || ds.name,
            type: ds.type,
            icon: PF_TYPE_ICONS[ds.type] || "bi-file-earmark-text",
            studies: [],
            pending: [],
            consented: [],
          });
          var scope = {
            studyId: study.id,
            codingSystem: row.code.codingSystem,
            codingCode: row.code.codingCode,
            text: row.code.text,
            label: pfPatientLabel(row.code.text),
            consentedTime: row.consentedTime,
            method: pending ? "POST" : "PATCH",
          };
          (pending || row.consented === false ? source.pending : source.consented).push(scope);
          if (source.studies.indexOf(study.name) === -1) source.studies.push(study.name);
        });
      });
    });
  }
  collect(consents.studiesPendingConsent, true);
  collect(consents.studies, false);
  return Object.keys(byId).map(function (id) {
    var source = byId[id];
    source.isConsented = source.pending.length === 0 && source.consented.length > 0;
    source.labels = pfUniqueSorted(source.pending.concat(source.consented).map(function (s) { return s.label; }));
    source.consentedLabels = pfUniqueSorted(source.consented.map(function (s) { return s.label; }));
    source.studies.sort();
    return source;
  }).sort(function (a, b) { return a.name < b.name ? -1 : a.name > b.name ? 1 : 0; });
}

// Test-only: drop the cached profile so a following test's own profile mock is used.
function pfResetPatient() {
  pfPatient = null;
}

async function pfPatientId() {
  if (!pfPatient) pfPatient = (await pfApi("GET", "users/profile")).patient;
  return pfPatient.id;
}

async function pfSourcesNow() {
  var consents = await pfApi("GET", "patients/" + (await pfPatientId()) + "/consents");
  return pfSources(consents, PATIENT_PORTAL_CONFIG);
}

async function pfSource(id) {
  return (await pfSourcesNow()).filter(function (s) { return String(s.id) === String(id); })[0];
}

// The consents API body per request method: never-asked scopes are POSTed, existing rows PATCHed, grouped by study.
function pfConsentWrites(scopes, consented) {
  var writes = { POST: {}, PATCH: {} };
  scopes.forEach(function (scope) {
    var byStudy = writes[scope.method];
    var entry = byStudy[scope.studyId] || (byStudy[scope.studyId] = { study_id: scope.studyId, scope_consents: [] });
    entry.scope_consents.push({ coding_system: scope.codingSystem, coding_code: scope.codingCode, consented: consented });
  });
  return { POST: Object.values(writes.POST), PATCH: Object.values(writes.PATCH) };
}

async function pfWriteConsents(scopes, consented) {
  var patientId = await pfPatientId();
  var writes = pfConsentWrites(scopes, consented);
  if (writes.POST.length) await pfApi("POST", "patients/" + patientId + "/consents", { study_scope_consents: writes.POST });
  if (writes.PATCH.length) await pfApi("PATCH", "patients/" + patientId + "/consents", { study_scope_consents: writes.PATCH });
}

async function pfFhirSources() {
  return (await pfApi("GET", "fhir_sources")).results;
}

// The newest FhirSource registered for a data source, or null.
function pfLatestFhirSource(fhirSources, dataSourceId) {
  return fhirSources
    .filter(function (fs) { return String(fs.dataSource) === String(dataSourceId); })
    .sort(function (a, b) { return b.id - a.id; })[0] || null;
}

function pfRecordCount(fhirSource) {
  var counts = fhirSource.resourceCounts || {};
  return Object.keys(counts).reduce(function (sum, type) { return sum + counts[type]; }, 0);
}

// "facility · labels · N records" once a FhirSource names a facility, else the scope labels.
function pfCardDesc(source, fhirSource) {
  var labels = source.labels.join(", ");
  if (!fhirSource || !fhirSource.facility) return labels;
  return fhirSource.facility + " · " + labels + " · " + pfRecordCount(fhirSource) + " records";
}

// Per-type synced counts, a zero row per expected-but-missing type, and the total.
function pfReceipt(counts, expectedTypes) {
  var synced = Object.keys(counts)
    .map(function (type) { return { label: pfResourceLabel(type), n: counts[type] }; })
    .sort(function (a, b) { return b.n - a.n || (a.label < b.label ? -1 : a.label > b.label ? 1 : 0); });
  var notSynced = expectedTypes
    .filter(function (type) { return !(type in counts); })
    .map(function (type) { return { label: pfResourceLabel(type), n: 0 }; })
    .sort(function (a, b) { return a.label < b.label ? -1 : a.label > b.label ? 1 : 0; });
  return { synced: synced, notSynced: notSynced, total: pfRecordCount({ resourceCounts: counts }) };
}

function pfSourceReceipt(fhirSource) {
  return fhirSource ? pfReceipt(fhirSource.resourceCounts || {}, PATIENT_PORTAL_CONFIG.expectedResourceTypes) : null;
}

function pfLatestConsented(sources) {
  return sources.reduce(function (latest, source) {
    var time = Math.max.apply(null, source.consented.map(function (s) { return Date.parse(s.consentedTime) || 0; }));
    return !latest || time > latest.time ? { source: source, time: time } : latest;
  }, null);
}

// ────────────────────────────────────────────────────
// Screens
// ────────────────────────────────────────────────────

var PF_RAIL_STEPS = ["Choose organization", "Sign in", "Import records"];

// The three-step rail with the active step and everything before it marked.
function pfRail(activeStep) {
  return {
    steps: PF_RAIL_STEPS.map(function (label, i) {
      var num = i + 1;
      return { num: num, label: label, cls: num === activeStep ? " is-active" : num < activeStep ? " is-done" : "" };
    }),
  };
}

async function renderImporting() {
  pfRender("t-importing", { rail: pfRail(3) });
}

async function renderHub() {
  var sources = await pfSourcesNow();
  var fhirSources = sources.some(function (s) { return s.isConsented; }) ? await pfFhirSources() : [];
  var studies = pfUniqueSorted([].concat.apply([], sources.map(function (s) { return s.studies; })));
  pfRender("t-hub", {
    eyebrow: studies.length === 1 ? studies[0] : "Your studies",
    cards: sources.map(function (source) {
      var fhirSource = source.isConsented ? pfLatestFhirSource(fhirSources, source.id) : null;
      return {
        id: source.id,
        title: source.name,
        desc: pfCardDesc(source, fhirSource),
        icon: source.icon,
        on: source.isConsented,
        badge: source.isConsented ? "Consented" : "Not consented",
        route: source.isConsented ? "manage" : "consent",
      };
    }),
  });
}

async function renderConsent(params) {
  var source = await pfSource(params.source);
  if (!source || !source.pending.length) return pfNav("hub", {}, true);
  pfRender("t-consent", {
    sourceId: source.id,
    eyebrow: [source.name].concat(source.studies).join(" · "),
    sourceName: source.name,
    rows: pfUniqueSorted(source.pending.map(function (s) { return s.label; })),
    scopeDetail: pfScopeDetail(PATIENT_PORTAL_CONFIG.expectedResourceTypes),
  });
}

// Record consent for the source's pending scopes, then hand off to the client's connect step.
async function pfAgree(sourceId) {
  pfShowLoading();
  try {
    await pfWriteConsents((await pfSource(sourceId)).pending, true);
  } catch (e) {
    pfHideLoading();
    showFlowError("We couldn't save your consent", e.message);
    return;
  }
  pfHideLoading();
  await pfNav("connect", { source: String(sourceId) });
}

async function renderConnect(params) {
  var source = await pfSource(params.source);
  if (!source) return pfNav("hub", {}, true);
  if (!source.isConsented) return pfNav("consent", { source: String(source.id) }, true);
  await pfClient.connect(source);
}

// The source just connected (?source=), else this client's most recently consented source.
async function renderDone(params) {
  var consented = (await pfSourcesNow()).filter(function (s) { return s.isConsented; });
  var primary = consented.filter(function (s) { return String(s.id) === String(params.source); })[0];
  if (!primary && consented.length) primary = pfLatestConsented(consented).source;
  var fhirSource = primary ? pfLatestFhirSource(await pfFhirSources(), primary.id) : null;
  var study = primary && primary.studies.length === 1 ? primary.studies[0] : "your study team";
  pfRender("t-done", {
    lede: primary
      ? "You've agreed to share your selected data with " + study + ". You can manage or disconnect any source anytime."
      : "Nothing is shared yet.",
    rows: primary ? [{ name: primary.name, detail: pfCardDesc(primary, fhirSource) }] : [],
    receipt: pfSourceReceipt(fhirSource),
  });
}

async function renderManage(params) {
  var source = await pfSource(params.source);
  if (!source || !source.consented.length) return pfNav("hub", {}, true);
  var fhirSource = pfLatestFhirSource(await pfFhirSources(), source.id);
  pfRender("t-manage", {
    sourceId: source.id,
    sourceName: source.name,
    icon: source.icon,
    detail: fhirSource && fhirSource.facility ? pfCardDesc(source, fhirSource) : null,
    rows: source.consentedLabels,
    receipt: pfSourceReceipt(fhirSource),
  });
}

// Revoke every consented scope of the source (rows stay, consented becomes false), then back to the hub.
async function pfStopSharing(sourceId) {
  pfShowLoading();
  try {
    await pfWriteConsents((await pfSource(sourceId)).consented, false);
  } catch (e) {
    pfHideLoading();
    showFlowError("We couldn't stop sharing", e.message);
    return;
  }
  pfHideLoading();
  await pfNav("hub");
}

// ────────────────────────────────────────────────────
// Routes
// ────────────────────────────────────────────────────

var PF_ROUTES = {
  hub: renderHub,
  consent: renderConsent,
  connect: renderConnect,
  importing: renderImporting,
  done: renderDone,
  manage: renderManage,
  error: renderError,
};

var PF_ROUTE_TITLES = {
  hub: "Choose how to share your data",
  consent: "What you'll share",
  connect: "Connect",
  importing: "Importing records",
  done: "You're all set",
  manage: "You're sharing",
  error: "Something went wrong",
};

// Exposed for unit tests; browser runs load this as a plain <script> and ignore it.
if (typeof window !== "undefined") {
  window.TOKEN_ENDPOINT = TOKEN_ENDPOINT;
  window.API_ENDPOINT = API_ENDPOINT;
  window.pfClient = pfClient;
  window.storeToken = storeToken;
  window.getStoredToken = getStoredToken;
  window.exchangeCodeForToken = exchangeCodeForToken;
  window.pfRedeemInvitation = pfRedeemInvitation;
  window.pfRouteAndParams = pfRouteAndParams;
  window.pfUrl = pfUrl;
  window.pfRender = pfRender;
  window.pfShowLoading = pfShowLoading;
  window.pfHideLoading = pfHideLoading;
  window.pfRegisterPartials = pfRegisterPartials;
  window.pfApi = pfApi;
  window.showFlowError = showFlowError;
  window.pfRetry = pfRetry;
  window.pfNav = pfNav;
  window.renderError = renderError;
  window.patientApp = patientApp;
  window.PF_ROUTES = PF_ROUTES;
  window.pfPatientLabel = pfPatientLabel;
  window.pfUniqueSorted = pfUniqueSorted;
  window.pfSources = pfSources;
  window.pfSourcesNow = pfSourcesNow;
  window.pfSource = pfSource;
  window.pfFhirSources = pfFhirSources;
  window.pfLatestFhirSource = pfLatestFhirSource;
  window.pfCardDesc = pfCardDesc;
  window.renderHub = renderHub;
  window.pfResourceLabel = pfResourceLabel;
  window.pfScopeDetail = pfScopeDetail;
  window.pfConsentWrites = pfConsentWrites;
  window.pfWriteConsents = pfWriteConsents;
  window.renderConsent = renderConsent;
  window.renderConnect = renderConnect;
  window.pfRail = pfRail;
  window.renderImporting = renderImporting;
  window.pfAgree = pfAgree;
  window.pfReceipt = pfReceipt;
  window.pfSourceReceipt = pfSourceReceipt;
  window.renderDone = renderDone;
  window.renderManage = renderManage;
  window.pfStopSharing = pfStopSharing;
  window.pfResetPatient = pfResetPatient;
}
