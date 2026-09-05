import { describe, test, expect, beforeAll, beforeEach, jest } from "@jest/globals";
const fs = require("fs");
const path = require("path");

const STATIC = path.resolve(__dirname, "../../../core/static");
const COMPONENTS = path.resolve(__dirname, "../../../core/templates/common/patient_facing/components");
const TEMPLATES_DIR = path.resolve(__dirname, "../../../core/templates");

// A Django component file is a {% verbatim %}-wrapped <script type="text/template">; strip the tags to get the HTML.
function componentHtml(file) {
  return fs.readFileSync(file, "utf8").replace(/{% ?verbatim ?%}|{% ?endverbatim ?%}/g, "");
}

// The consents payload as the API camelCases it; Oura (3) is this client's, EHR Patient Portal (5) is not.
const OURA = { id: 3, name: "Oura", type: "personal_device", supportedScopes: [{ id: 21, text: "Sleep episode (IEEE)" }, { id: 22, text: "Heart Rate (OMH)" }] };
const EHR = { id: 5, name: "EHR Patient Portal", type: "patient_app", supportedScopes: [{ id: 30, text: "Clinical records" }] };
const CAREX = { id: 7, name: "CareX", type: "patient_app", supportedScopes: [{ id: 22, text: "Heart Rate (OMH)" }, { id: 23, text: "Blood pressure (OMH)" }] };
function scope(id, text, codingCode, consented, consentedTime) {
  return { code: { id: id, codingSystem: "sys", codingCode: codingCode, text: text }, consented: consented, consentedTime: consentedTime || null };
}
const CONSENTS = {
  studiesPendingConsent: [
    { id: 100, name: "Lifespan Study on BP & HR", dataSources: [CAREX, EHR], pendingScopeConsents: [scope(30, "Clinical records", "*", null)] },
  ],
  studies: [
    { id: 101, name: "Lifespan Study on Sleep & BP", dataSources: [CAREX, OURA], scopeConsents: [scope(21, "Sleep episode (IEEE)", "ieee:sleep-episode:1.0", true, "2026-09-01T00:00:00Z"), scope(23, "Blood pressure (OMH)", "omh:blood-pressure:4.0", true, "2026-09-01T00:00:00Z")] },
    { id: 100, name: "Lifespan Study on BP & HR", dataSources: [CAREX, EHR], scopeConsents: [scope(22, "Heart Rate (OMH)", "omh:heart-rate:2.0", true, "2026-09-01T00:00:00Z")] },
  ],
};
// The same payload after Clinical records was consented: study 100 no longer pending, the row consented.
const CONSENTS_AFTER = {
  studiesPendingConsent: [],
  studies: CONSENTS.studies.map((study) =>
    study.id === 100 ? Object.assign({}, study, { scopeConsents: study.scopeConsents.concat([scope(30, "Clinical records", "*", true, "2026-09-02T00:00:00Z")]) }) : study
  ),
};
const OW_CONFIG = { dataSourceIds: [3], sourceLabels: { 3: "Oura" } };
const EHR_CONFIG = { dataSourceIds: [5], sourceLabels: { 5: "EHR Patient Portal" } };

beforeAll(() => {
  global.Handlebars = require(path.join(STATIC, "common/js/handlebars.min.js"));
  require(path.join(STATIC, "common/js/common.js"));
  require(path.join(STATIC, "common/js/patient-facing.js"));
});

beforeEach(() => {
  global.PATIENT_PORTAL_CONFIG = { client: "ow", pageUrl: "/clients/ow/launch", siteTitle: "JupyterHealth Exchange", dataSourceIds: [3], sourceLabels: { 3: "Oura" }, expectedResourceTypes: [] };
  document.body.innerHTML = `<div id="pf_main"></div><div id="navLoadingOverlay" style="display:none"></div>` + componentHtml(path.join(COMPONENTS, "error.html"));
  window.sessionStorage.clear();
  window.pfResetPatient();
  global.fetch = jest.fn();
});

describe("pfRouteAndParams", () => {
  test("defaults to the hub and strips the route key from params", () => {
    expect(window.pfRouteAndParams("")).toEqual({ route: "hub", params: {} });
    expect(window.pfRouteAndParams("?route=consent&source=12")).toEqual({ route: "consent", params: { source: "12" } });
  });

  test("pfUrl round-trips route and params on the current page path", () => {
    window.history.replaceState({}, "", "/clients/ow/launch?route=hub");
    expect(window.pfUrl("manage", { source: "3" })).toBe("/clients/ow/launch?route=manage&source=3");
  });
});

describe("token helpers", () => {
  test("stores and reads the token from sessionStorage", () => {
    window.storeToken("tok");
    expect(window.getStoredToken()).toBe("tok");
    expect(window.sessionStorage.getItem("pf_access_token")).toBe("tok");
  });
});

describe("pfRedeemInvitation", () => {
  test("redeems the code, exchanges the grant and stores the token", async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ grant: { client_id: "cid", code: "authcode", redirect_uri: "http://x/cb" } }) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ access_token: "jwt" }) });

    await window.pfRedeemInvitation("localhost%3A8001_abc");

    expect(window.getStoredToken()).toBe("jwt");
    expect(global.fetch.mock.calls[0][0]).toBe("http://localhost:8001/api/v1/invitation/abc");
    const tokenBody = global.fetch.mock.calls[1][1].body;
    expect(tokenBody).toContain("grant_type=authorization_code");
    expect(tokenBody).toContain("client_id=cid");
    expect(tokenBody).toContain("code_verifier=" + btoa("abc").replace(/=/g, ""));
  });

  test("throws the patient-readable message when the code is malformed or refused", async () => {
    await expect(window.pfRedeemInvitation("nounderscore")).rejects.toThrow("Ask your study team for a new link");
    global.fetch = jest.fn().mockResolvedValueOnce({ ok: false, status: 409 });
    await expect(window.pfRedeemInvitation("h_t")).rejects.toThrow("Ask your study team for a new link");
    expect(window.getStoredToken()).toBeNull();
  });
});

describe("pfApi", () => {
  test("adds the bearer token, JSON-encodes the body and parses the response", async () => {
    window.storeToken("tok");
    global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({ id: 1 }) });

    const data = await window.pfApi("POST", "patients/7/consents", { study_scope_consents: [] });

    expect(data).toEqual({ id: 1 });
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toBe("http://localhost/api/v1/patients/7/consents");
    expect(opts.method).toBe("POST");
    expect(opts.headers.Authorization).toBe("Bearer tok");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opts.body)).toEqual({ study_scope_consents: [] });
  });

  test("throws with the API's error text on a failed response", async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 403, statusText: "Forbidden", json: () => Promise.resolve({ detail: "nope" }) });
    await expect(window.pfApi("GET", "users/profile")).rejects.toThrow("nope");
  });
});

describe("showFlowError and pfRender", () => {
  test("renders the error callout with Try again and Back on the client page", () => {
    window.showFlowError("We couldn't connect", "boom");
    const main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-error__title").textContent).toBe("We couldn't connect");
    expect(main.querySelector(".pf-error__msg").textContent).toBe("boom");
    expect(main.querySelector("#pf_error_wrap .pf-actions")).not.toBeNull();
    expect(main.textContent).toContain("Try again");
    expect(main.querySelector(".pf-btn--ghost").getAttribute("href")).toBe("/clients/ow/launch");
  });

  test("uses a plain link for the retry when a retryHref is given", () => {
    window.showFlowError("t", "m", { retryLabel: "Choose a different organization", retryHref: "/clients/ehr-patient-portal/?route=connect&source=5" });
    const retry = document.querySelector("#pf_main .pf-btn");
    expect(retry.textContent).toBe("Choose a different organization");
    expect(retry.getAttribute("href")).toBe("/clients/ehr-patient-portal/?route=connect&source=5");
  });

  test("escapes context values", () => {
    window.pfRender("t-error", { title: "<b>x</b>", message: "" });
    expect(document.getElementById("pf_main").innerHTML).toContain("&lt;b&gt;x&lt;/b&gt;");
  });
});

describe("patientApp", () => {
  test("says the session has ended when there is no code and no token", async () => {
    window.history.replaceState({}, "", "/clients/ow/launch");
    await window.patientApp();
    expect(document.querySelector(".pf-error__title").textContent).toBe("Your session has ended");
    expect(document.querySelector(".pf-error__msg").textContent).toBe("Open the link from your invitation email to continue.");
  });

  test("renders the error route from the query string", async () => {
    window.storeToken("tok");
    window.history.replaceState({}, "", "/clients/ow/launch?route=error&title=We+couldn%27t+connect+your+wearable&message=access_denied");
    await window.patientApp();
    expect(document.querySelector(".pf-error__title").textContent).toBe("We couldn't connect your wearable");
    expect(document.querySelector(".pf-error__msg").textContent).toBe("access_denied");
    // Re-rendering this route would show the same error, so Try again restarts the client's connect step.
    expect(document.querySelector("#pf_main .pf-btn").getAttribute("href")).toBe("/clients/ow/launch?route=connect&source=3");
  });

  test("drops the code from the URL after redeeming and navigates to the route", async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ grant: { client_id: "c", code: "a", redirect_uri: "r" } }) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ access_token: "jwt" }) });
    window.history.replaceState({}, "", "/clients/ow/launch?code=h_t&route=error&title=T&message=M");

    await window.patientApp();

    expect(window.location.search).toBe("?route=error&title=T&message=M");
    expect(document.querySelector(".pf-error__title").textContent).toBe("T");
  });
});

describe("pfSources", () => {
  test("lists only this client's sources, with the scopes their studies request through them", () => {
    const sources = window.pfSources(CONSENTS, OW_CONFIG);
    expect(sources.map((s) => s.id)).toEqual([3]);
    const oura = sources[0];
    expect(oura.isConsented).toBe(true);
    expect(oura.labels).toEqual(["Sleep episode"]);  // Heart Rate is requested via CareX, never through Oura
    expect(oura.studies).toEqual(["Lifespan Study on Sleep & BP"]);
    expect(oura.icon).toBe("bi-smartwatch");
    expect(oura.consented[0]).toMatchObject({ studyId: 101, codingCode: "ieee:sleep-episode:1.0", method: "PATCH" });
  });

  test("a never-asked scope is pending with POST and the source is not consented", () => {
    const [ehr] = window.pfSources(CONSENTS, EHR_CONFIG);
    expect(ehr.isConsented).toBe(false);
    expect(ehr.pending).toEqual([expect.objectContaining({ studyId: 100, codingCode: "*", label: "Clinical records", method: "POST" })]);
    expect(ehr.icon).toBe("bi-file-earmark-text");
  });

  test("a revoked row is pending with PATCH", () => {
    const revoked = { studiesPendingConsent: [], studies: [{ id: 101, name: "S", dataSources: [OURA], scopeConsents: [scope(21, "Sleep episode (IEEE)", "ieee:sleep-episode:1.0", false)] }] };
    const [oura] = window.pfSources(revoked, OW_CONFIG);
    expect(oura.isConsented).toBe(false);
    expect(oura.consented).toEqual([]);
    expect(oura.pending[0].method).toBe("PATCH");
  });

  test("a source with nothing requested through it is not listed", () => {
    expect(window.pfSources({ studiesPendingConsent: [], studies: [] }, OW_CONFIG)).toEqual([]);
  });
});

describe("card description", () => {
  test("joins the labels, and leads with the facility and record count once a FhirSource has one", () => {
    const [oura] = window.pfSources(CONSENTS, OW_CONFIG);
    expect(window.pfCardDesc(oura, null)).toBe("Sleep episode");
    expect(window.pfCardDesc(oura, { facility: "Epic Sandbox", resourceCounts: { Observation: 3, Patient: 1 } })).toBe("Epic Sandbox · Sleep episode · 4 records");
    expect(window.pfCardDesc(oura, { facility: "", resourceCounts: {} })).toBe("Sleep episode");
  });

  test("pfLatestFhirSource picks the newest source registered for the data source", () => {
    const rows = [{ id: 1, dataSource: 5 }, { id: 9, dataSource: 5 }, { id: 4, dataSource: 3 }];
    expect(window.pfLatestFhirSource(rows, 5).id).toBe(9);
    expect(window.pfLatestFhirSource(rows, 8)).toBeNull();
  });
});

describe("renderHub", () => {
  test("renders one card per source with the consent badge and the single study as eyebrow", async () => {
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "hub.html"));
    window.storeToken("tok");
    global.fetch = jest.fn((url) => {
      if (url.includes("users/profile")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ id: 1, patient: { id: 40001 } }) });
      if (url.includes("/consents")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(CONSENTS) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ results: [] }) });
    });

    await window.renderHub();

    expect(global.fetch.mock.calls.some(([url]) => url.endsWith("/patients/40001/consents"))).toBe(true);
    const main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-eyebrow").textContent).toBe("Lifespan Study on Sleep & BP");
    expect(main.querySelector(".pf-card__title").textContent).toBe("Oura");
    expect(main.querySelector(".pf-card__badge").textContent).toBe("Consented");
    expect(main.querySelector("a.pf-card-link").getAttribute("onclick")).toContain("pfNav('manage'");
  });
});

describe("scope detail", () => {
  test("labels resource types, Demographics first, the rest lowercased", () => {
    expect(window.pfScopeDetail(["Observation", "Patient", "AllergyIntolerance", "MedicationRequest"])).toBe("Demographics, allergies, medications, observations");
    expect(window.pfScopeDetail([])).toBe("");
    expect(window.pfResourceLabel("DiagnosticReport")).toBe("Diagnostic reports");
    expect(window.pfResourceLabel("Goal")).toBe("Goals");
  });
});

describe("pfConsentWrites", () => {
  test("groups scopes by study under their request method in the API body shape", () => {
    const scopes = [
      { studyId: 100, codingSystem: "sys", codingCode: "*", method: "POST" },
      { studyId: 100, codingSystem: "sys", codingCode: "qr", method: "POST" },
      { studyId: 101, codingSystem: "sys", codingCode: "sleep", method: "PATCH" },
    ];
    expect(window.pfConsentWrites(scopes, true)).toEqual({
      POST: [{ study_id: 100, scope_consents: [{ coding_system: "sys", coding_code: "*", consented: true }, { coding_system: "sys", coding_code: "qr", consented: true }] }],
      PATCH: [{ study_id: 101, scope_consents: [{ coding_system: "sys", coding_code: "sleep", consented: true }] }],
    });
  });
});

describe("consent flow", () => {
  // GET consents returns `consents` until a write happens, then `after` (the API reflects the new rows).
  function consentsFetch(consents, calls, after) {
    let written = false;
    return jest.fn((url, opts) => {
      calls.push([url, opts]);
      if (url.includes("users/profile")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ patient: { id: 40001 } }) });
      if (url.includes("/consents") && opts.method === "GET") return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(written && after ? after : consents) });
      written = true;
      return Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve({}) });
    });
  }

  test("renderConsent shows the pending scopes for the source and the client's scope detail", async () => {
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "consent.html"));
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, EHR_CONFIG, { pageUrl: "/clients/ehr-patient-portal/", siteTitle: "T", expectedResourceTypes: ["Patient", "Observation"] });
    global.fetch = consentsFetch(CONSENTS, []);

    await window.renderConsent({ source: "5" });

    const main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-eyebrow").textContent).toBe("EHR Patient Portal · Lifespan Study on BP & HR");
    expect(main.querySelector(".pf-card__title").textContent).toBe("Clinical records");
    expect(main.querySelector(".pf-card__desc").textContent).toBe("Demographics, observations");
  });

  test("pfAgree writes the pending scopes and moves to the connect route", async () => {
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, EHR_CONFIG, { pageUrl: "/clients/ehr-patient-portal/", siteTitle: "T", expectedResourceTypes: [] });
    const calls = [];
    global.fetch = consentsFetch(CONSENTS, calls, CONSENTS_AFTER);
    window.pfClient.connect = jest.fn();
    window.history.replaceState({}, "", "/clients/ehr-patient-portal/?route=consent&source=5");

    await window.pfAgree("5");

    const post = calls.find(([, opts]) => opts && opts.method === "POST");
    expect(post[0]).toContain("/patients/40001/consents");
    expect(JSON.parse(post[1].body)).toEqual({ study_scope_consents: [{ study_id: 100, scope_consents: [{ coding_system: "sys", coding_code: "*", consented: true }] }] });
    expect(calls.some(([, opts]) => opts && opts.method === "PATCH")).toBe(false);
    expect(window.location.search).toBe("?route=connect&source=5");
    expect(window.pfClient.connect).toHaveBeenCalledWith(expect.objectContaining({ id: 5, isConsented: true }));
  });

  test("renderConnect sends an unconsented source to the consent screen first, else calls the client hook", async () => {
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, OW_CONFIG, { pageUrl: "/clients/ow/launch", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = consentsFetch(CONSENTS, []);
    window.pfClient.connect = jest.fn();
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "consent.html"));

    await window.renderConnect({ source: "3" });
    expect(window.pfClient.connect).toHaveBeenCalledWith(expect.objectContaining({ id: 3, isConsented: true }));

    global.PATIENT_PORTAL_CONFIG = Object.assign({}, EHR_CONFIG, { pageUrl: "/clients/ehr-patient-portal/", siteTitle: "T", expectedResourceTypes: [] });
    window.pfClient.connect.mockClear();
    await window.renderConnect({ source: "5" });
    expect(window.pfClient.connect).not.toHaveBeenCalled();
    expect(window.location.search).toBe("?route=consent&source=5");
  });
});

describe("Open Wearables hook", () => {
  beforeAll(() => {
    require(path.join(STATIC, "clients/ow/js/client-ow.js"));
  });

  test("connect renders the launch card with the consented labels; Continue creates the OW user and redirects to Oura", async () => {
    document.body.innerHTML += fs.readFileSync(path.resolve(TEMPLATES_DIR, "clients/ow/components/launch.html"), "utf8").replace(/{% ?verbatim ?%}|{% ?endverbatim ?%}/g, "");
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG.siteTitle = "Meridian Exchange";
    const [oura] = window.pfSources(CONSENTS, OW_CONFIG);

    await window.pfClient.connect(oura);

    const main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-h1").textContent).toBe("Connect your Oura");
    expect(main.querySelector(".pf-lede").textContent).toContain("Meridian Exchange never sees your Oura username or password");
    expect(main.querySelector(".pf-card__desc").textContent).toBe("Sleep episode");
    expect(main.querySelector("#ow_continue").textContent).toBe("Continue to Oura");
    expect(main.querySelector(".pf-btn--ghost").getAttribute("onclick")).toContain("pfNav('hub')");

    global.fetch = jest.fn((url) => {
      if (url.includes("ow/users")) return Promise.resolve({ ok: true, json: () => Promise.resolve({ ow_user_id: "u1" }) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ authorizationUrl: "https://oura.example/authorize" }) });
    });

    await window.owContinue();

    expect(global.fetch.mock.calls[0][0]).toBe("http://localhost/api/v1/ow/users");
    expect(global.fetch.mock.calls[0][1].headers.Authorization).toBe("Bearer tok");
    expect(global.fetch.mock.calls[1][0]).toContain("redirect_uri=http%3A%2F%2Flocalhost%2Fclients%2Fow%2Fcomplete");
  });
  // jsdom cannot navigate, so the final `window.location.href = ...` logs "Not implemented: navigation"; that is the redirect.
});

describe("pfReceipt", () => {
  test("sorts synced rows by count then label, lists expected-but-missing types as not synced, and totals", () => {
    const receipt = window.pfReceipt({ Observation: 5, Patient: 1, Condition: 5 }, ["Patient", "Observation", "AllergyIntolerance", "Condition"]);
    expect(receipt.synced).toEqual([{ label: "Conditions", n: 5 }, { label: "Observations", n: 5 }, { label: "Demographics", n: 1 }]);
    expect(receipt.notSynced).toEqual([{ label: "Allergies", n: 0 }]);
    expect(receipt.total).toBe(11);
  });
});

describe("renderDone", () => {
  test("shows the source named in the URL with its receipt, and names its single study", async () => {
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "done.html")) + componentHtml(path.join(COMPONENTS, "receipt.html"));
    window.pfRegisterPartials();
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, OW_CONFIG, { pageUrl: "/clients/ow/launch", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = jest.fn((url) => {
      if (url.includes("users/profile")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ patient: { id: 40001 } }) });
      if (url.includes("/consents")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(CONSENTS) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ results: [{ id: 2, dataSource: 3, facility: "Oura Cloud", resourceCounts: { Observation: 7 } }] }) });
    });

    await window.renderDone({ source: "3" });

    const main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-lede").textContent).toBe("You've agreed to share your selected data with Lifespan Study on Sleep & BP. You can manage or disconnect any source anytime.");
    expect(main.querySelector(".pf-consent-row__label").textContent).toBe("Oura · Oura Cloud · Sleep episode · 7 records");
    expect(main.querySelector(".pf-receipt__row--total .pf-receipt__n").textContent).toBe("7");
  });

  test("with no source in the URL it falls back to the client's consented source and its receipt", async () => {
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "done.html")) + componentHtml(path.join(COMPONENTS, "receipt.html"));
    window.pfRegisterPartials();
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, OW_CONFIG, { pageUrl: "/clients/ow/launch", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = jest.fn((url) => {
      if (url.includes("users/profile")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ patient: { id: 40001 } }) });
      if (url.includes("/consents")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(CONSENTS) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ results: [{ id: 2, dataSource: 3, facility: "Oura Cloud", resourceCounts: { Observation: 7 } }] }) });
    });

    await window.renderDone({});

    const main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-lede").textContent).toBe("You've agreed to share your selected data with Lifespan Study on Sleep & BP. You can manage or disconnect any source anytime.");
    expect(main.querySelector(".pf-consent-row__label").textContent).toBe("Oura · Oura Cloud · Sleep episode · 7 records");
  });

  test("with two consented sources the most recently consented one is shown", async () => {
    // Both sources belong to this client, so the tie is broken on consentedTime, not on listing order.
    const TWO = {
      studiesPendingConsent: [],
      studies: [
        { id: 201, name: "Earlier Study", dataSources: [OURA], scopeConsents: [scope(21, "Sleep episode (IEEE)", "ieee:sleep-episode:1.0", true, "2026-08-01T00:00:00Z")] },
        { id: 202, name: "Later Study", dataSources: [CAREX], scopeConsents: [scope(23, "Blood pressure (OMH)", "omh:blood-pressure:4.0", true, "2026-09-03T00:00:00Z")] },
      ],
    };
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "done.html")) + componentHtml(path.join(COMPONENTS, "receipt.html"));
    window.pfRegisterPartials();
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = { pageUrl: "/clients/ow/launch", siteTitle: "T", expectedResourceTypes: [], dataSourceIds: [3, 7], sourceLabels: { 3: "Oura", 7: "CareX" } };
    global.fetch = jest.fn((url) => {
      if (url.includes("users/profile")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ patient: { id: 40001 } }) });
      if (url.includes("/consents")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(TWO) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ results: [] }) });
    });

    await window.renderDone({});

    const main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-lede").textContent).toContain("Later Study");
    expect(main.querySelector(".pf-consent-row__label").textContent).toBe("CareX · Blood pressure");
  });

  test("with nothing consented it says nothing is shared", async () => {
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "done.html")) + componentHtml(path.join(COMPONENTS, "receipt.html"));
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, EHR_CONFIG, { pageUrl: "/clients/ehr-patient-portal/", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = jest.fn((url) => {
      if (url.includes("users/profile")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ patient: { id: 40001 } }) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(CONSENTS) });
    });

    await window.renderDone({});

    expect(document.querySelector("#pf_main .pf-lede").textContent).toBe("Nothing is shared yet.");
    expect(document.querySelector("#pf_main .pf-receipt")).toBeNull();
  });
});

describe("manage flow", () => {
  function fetchWith(calls, fhirRows) {
    return jest.fn((url, opts) => {
      calls.push([url, opts]);
      if (url.includes("users/profile")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ patient: { id: 40001 } }) });
      if (url.includes("/consents") && !(opts && opts.method === "PATCH")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(CONSENTS) });
      if (url.includes("fhir_sources")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ results: fhirRows }) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });
  }

  test("renderManage lists the consented scopes, or the facility card and receipt when records exist", async () => {
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "manage.html")) + componentHtml(path.join(COMPONENTS, "receipt.html"));
    window.pfRegisterPartials();
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, OW_CONFIG, { pageUrl: "/clients/ow/launch", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = fetchWith([], []);

    await window.renderManage({ source: "3" });
    let main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-card__title").textContent).toBe("Sleep episode");
    expect(main.querySelector(".pf-receipt")).toBeNull();

    global.fetch = fetchWith([], [{ id: 2, dataSource: 3, facility: "Oura Cloud", resourceCounts: { Observation: 7 } }]);
    await window.renderManage({ source: "3" });
    main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-card__desc").textContent).toBe("Oura Cloud · Sleep episode · 7 records");
    expect(main.querySelector(".pf-receipt__row--total .pf-receipt__n").textContent).toBe("7");
  });

  test("pfStopSharing PATCHes every consented scope to false and returns to the hub", async () => {
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, OW_CONFIG, { pageUrl: "/clients/ow/launch", siteTitle: "T", expectedResourceTypes: [] });
    const calls = [];
    global.fetch = fetchWith(calls, []);
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "hub.html"));
    window.history.replaceState({}, "", "/clients/ow/launch?route=manage&source=3");

    await window.pfStopSharing("3");

    const patch = calls.find(([, opts]) => opts && opts.method === "PATCH");
    expect(JSON.parse(patch[1].body)).toEqual({ study_scope_consents: [{ study_id: 101, scope_consents: [{ coding_system: "sys", coding_code: "ieee:sleep-episode:1.0", consented: false }] }] });
    expect(window.location.search).toBe("?route=hub");
  });
});

describe("history and route guards", () => {
  function guardFetch(consents) {
    return jest.fn((url) => {
      if (url.includes("users/profile")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ patient: { id: 40001 } }) });
      if (url.includes("/consents")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(consents) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ results: [] }) });
    });
  }

  test("renderConsent bounces an already-consented source to the hub without adding a history step", async () => {
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "hub.html"));
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, OW_CONFIG, { pageUrl: "/clients/ow/launch", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = guardFetch(CONSENTS);
    window.history.replaceState({}, "", "/clients/ow/launch?route=consent&source=3");
    const depth = window.history.length;

    await window.pfNav("consent", { source: "3" });

    // A pushed redirect would trap Back on the screen the patient was just bounced off.
    expect(window.location.search).toBe("?route=hub");
    expect(window.history.length).toBe(depth);
  });

  test("renderManage bounces a source with nothing consented to the hub", async () => {
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "hub.html"));
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, EHR_CONFIG, { pageUrl: "/clients/ehr-patient-portal/", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = guardFetch(CONSENTS);
    window.history.replaceState({}, "", "/clients/ehr-patient-portal/?route=manage&source=5");

    await window.renderManage({ source: "5" });

    expect(window.location.search).toBe("?route=hub");
  });

  test("a route name inherited from Object falls back to the hub", async () => {
    document.body.innerHTML += componentHtml(path.join(COMPONENTS, "hub.html"));
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, OW_CONFIG, { pageUrl: "/clients/ow/launch", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = guardFetch(CONSENTS);
    window.history.replaceState({}, "", "/clients/ow/launch?route=constructor");

    await window.pfNav("constructor", {});

    expect(window.location.search).toBe("?route=hub");
    expect(document.querySelector("#pf_main .pf-card__title").textContent).toBe("Oura");
  });
});

describe("failure branches", () => {
  test("a failed load shows the error callout and hides the loading overlay", async () => {
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, OW_CONFIG, { pageUrl: "/clients/ow/launch", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 401, statusText: "Unauthorized", json: () => Promise.resolve({ detail: "Invalid token" }) }));
    window.history.replaceState({}, "", "/clients/ow/launch?route=manage&source=3");
    // pfNav logs the caught error; keep it out of the suite output.
    const logged = jest.spyOn(console, "error").mockImplementation(() => {});

    await window.pfNav("manage", { source: "3" });
    logged.mockRestore();

    expect(document.querySelector(".pf-error__title").textContent).toBe("Something went wrong");
    expect(document.querySelector(".pf-error__msg").textContent).toBe("Invalid token");
    expect(document.getElementById("navLoadingOverlay").style.display).toBe("none");
  });

  test("a rejected consent write leaves the patient where they were, with a callout", async () => {
    window.storeToken("tok");
    global.PATIENT_PORTAL_CONFIG = Object.assign({}, EHR_CONFIG, { pageUrl: "/clients/ehr-patient-portal/", siteTitle: "T", expectedResourceTypes: [] });
    global.fetch = jest.fn((url, opts) => {
      if (url.includes("users/profile")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ patient: { id: 40001 } }) });
      if (opts && opts.method === "POST") return Promise.resolve({ ok: false, status: 400, statusText: "Bad Request", json: () => Promise.resolve({ detail: "scope not requested" }) });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(CONSENTS) });
    });
    window.history.replaceState({}, "", "/clients/ehr-patient-portal/?route=consent&source=5");

    await window.pfAgree("5");

    expect(document.querySelector(".pf-error__title").textContent).toBe("We couldn't save your consent");
    expect(document.querySelector(".pf-error__msg").textContent).toBe("scope not requested");
    expect(window.location.search).toBe("?route=consent&source=5");
  });
});
