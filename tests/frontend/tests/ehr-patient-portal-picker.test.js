import { describe, test, expect, beforeAll, beforeEach, jest } from "@jest/globals";
const fs = require("fs");
const path = require("path");

const STATIC = path.resolve(__dirname, "../../../core/static");
const TEMPLATES = path.resolve(__dirname, "../../../core/templates");

// A Django component file is a {% verbatim %}-wrapped <script type="text/template">; strip the tags to get the HTML.
function componentHtml(file) {
  return fs.readFileSync(file, "utf8").replace(/{% ?verbatim ?%}|{% ?endverbatim ?%}/g, "");
}

// The connect page as the shell serves it: the shared rail partial plus this client's picker component.
function renderPickerPage() {
  document.body.innerHTML =
    `<div id="pf_main"></div>` +
    componentHtml(path.join(TEMPLATES, "common/patient_facing/components/rail.html")) +
    componentHtml(path.join(TEMPLATES, "clients/ehr-patient-portal/components/connect.html"));
  window.pfRegisterPartials();
}

// Loads patient-facing.js (router, pfRender, pfRail, ...) then client-ehr-patient-portal.js,
// which exposes the picker helpers and the connect step on window.
beforeAll(() => {
  global.Handlebars = require(path.join(STATIC, "common/js/handlebars.min.js"));
  require(path.join(STATIC, "common/js/common.js"));
  require(path.join(STATIC, "common/js/patient-facing.js"));
  require("../../../core/static/clients/ehr-patient-portal/js/client-ehr-patient-portal.js");
});

beforeEach(() => {
  global.fetch = jest.fn();
  delete global.FHIR;
  global.PATIENT_FACING_CONFIG = { dataSourceIds: [5], pageUrl: "/clients/ehr-patient-portal/", siteTitle: "T", expectedResourceTypes: [] };
  // The scope picker's catalog + always-required plumbing scopes, normally injected by server-settings.js.
  global.CONSTANTS = {
    EHR_SUPPORTED_SCOPES: { "patient/Patient.read": "Demographics", "patient/Condition.read": "Conditions" },
    EHR_PATIENT_PORTAL_BASE_SCOPES: "openid profile launch/patient",
  };
});

describe("eppSearchBrands", () => {
  test("queries the brands API with the JHE token and returns the results", async () => {
    const rows = [{ brandName: "Mount Sinai", facilityName: "MSH", fhirBaseUrl: "https://s/FHIR/R4", addressText: "NY" }];
    global.fetch = jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ results: rows }) }));

    const out = await window.eppSearchBrands("tok", "sinai");

    expect(out).toEqual(rows);
    const [url, opts] = global.fetch.mock.calls[0];
    // Hyphen, matching the route in core/urls.py. An underscore 404s and eppSearchBrands
    // swallows it into [], so the picker just says "no hospitals found".
    expect(url).toContain("/api/v1/ehr-patient-portal/brands");
    expect(url).toContain("q=sinai");
    expect(opts.headers.Authorization).toBe("Bearer tok");
  });

  test("returns [] on a failed response", async () => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: false }));
    const out = await window.eppSearchBrands("tok", "x");
    expect(out).toEqual([]);
  });
});

describe("eppAuthorizeWithIss", () => {
  test("launches SMART authorize with the given client id, scope and hospital iss", () => {
    const authorize = jest.fn();
    global.FHIR = { oauth2: { authorize } };

    window.eppAuthorizeWithIss("cid", "launch/patient", "https://sinai/FHIR/R4");

    expect(authorize).toHaveBeenCalledTimes(1);
    const arg = authorize.mock.calls[0][0];
    expect(arg.iss).toBe("https://sinai/FHIR/R4");
    expect(arg.clientId).toBe("cid");
    expect(arg.scope).toBe("launch/patient");
    expect(arg.redirectUri).toContain("/clients/ehr-patient-portal/callback");
  });
});

describe("eppRenderBrandResults", () => {
  test("renders a clickable row per result and fires onSelect with that result", () => {
    const container = document.createElement("div");
    const rows = [
      { brandName: "Mount Sinai", facilityName: "MSH", fhirBaseUrl: "https://a/FHIR/R4", addressText: "1 Levy Pl" },
      { brandName: "Mercy", facilityName: "Mercy STL", fhirBaseUrl: "https://b/FHIR/R4", addressText: "MO" },
    ];
    const onSelect = jest.fn();

    const n = window.eppRenderBrandResults(container, rows, onSelect);

    expect(n).toBe(2);
    const items = container.querySelectorAll("[data-brand-result]");
    expect(items.length).toBe(2);
    // the row shows the hospital name + address to the patient
    expect(items[0].textContent).toContain("Mount Sinai");
    expect(items[0].textContent).toContain("1 Levy Pl");
    items[1].click();
    expect(onSelect).toHaveBeenCalledWith(rows[1]);
  });

  test("shows a no-results message when empty", () => {
    const container = document.createElement("div");
    const n = window.eppRenderBrandResults(container, [], jest.fn());
    expect(n).toBe(0);
    expect(container.textContent.toLowerCase()).toContain("no ");
  });
});

describe("eppImportFailure", () => {
  test("is the last Error line, or the all-types-failed message, or null", () => {
    expect(window.eppImportFailure("Fetching Labs\n  could not fetch Labs: 403\nFetching Vitals\n  could not fetch Vitals: 403")).toBe("none of your record types could be fetched");
    expect(window.eppImportFailure("Fetching Labs\n  saved 3 record(s)\n  could not fetch Vitals: 403")).toBeNull();
    expect(window.eppImportFailure("Completing connection...\nError: no JHE session. Restart from your invitation link.")).toBe("no JHE session. Restart from your invitation link.");
  });
});

describe("pfClient.connect on the EHR page", () => {
  test("renders the picker at rail step 1, then the scope picker, then authorizes with the brand's own client id and chosen scopes", async () => {
    renderPickerPage();
    window.storeToken("tok");
    global.fetch = jest.fn(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            results: [
              {
                id: 9,
                brandName: "Epic Sandbox",
                facilityName: "Madison",
                fhirBaseUrl: "https://epic/FHIR/R4",
                addressText: "WI",
                ehrClientId: "brand-cid",
                supportedScopes: "patient/Patient.read patient/Condition.read",
              },
            ],
          }),
      }),
    );

    await window.pfClient.connect({ id: 5, name: "EHR Patient Portal" });

    const main = document.getElementById("pf_main");
    expect(main.querySelector(".pf-rail__step").className).toBe("pf-rail__step is-active");
    expect(main.querySelector(".pf-h1").textContent).toBe("Share your medical records");
    expect(main.querySelector("#hospital-picker").hidden).toBe(false);
    expect(main.querySelectorAll("#hospital-results [data-brand-result]")).toHaveLength(1);
    const authorize = jest.fn();
    global.FHIR = { oauth2: { authorize } };
    main.querySelector("#hospital-results [data-brand-result]").click();

    // Picking a hospital swaps the picker for a checklist of that brand's supported scopes, all pre-checked.
    expect(main.querySelector("#hospital-picker").hidden).toBe(true);
    expect(main.querySelector("#scope-picker").hidden).toBe(false);
    const checkboxes = main.querySelectorAll("#scope-picker-list input[type=checkbox]");
    expect(checkboxes).toHaveLength(2);
    expect([...checkboxes].every((c) => c.checked)).toBe(true);
    expect(authorize).not.toHaveBeenCalled();
    // The focused row was hidden, so focus moves into the newly shown checklist, which is labelled as a group.
    expect(document.activeElement).toBe(checkboxes[0]);
    const group = main.querySelector("#scope-picker-list");
    expect(group.getAttribute("role")).toBe("group");
    expect(document.getElementById(group.getAttribute("aria-labelledby")).textContent).toBe("Choose what to share");

    document.getElementById("scope-picker-continue").click();

    expect(authorize).toHaveBeenCalledTimes(1);
    const arg = authorize.mock.calls[0][0];
    expect(arg.iss).toBe("https://epic/FHIR/R4");
    expect(arg.clientId).toBe("brand-cid");
    expect(arg.scope).toBe("openid profile launch/patient patient/Patient.read patient/Condition.read");
    expect(window.sessionStorage.getItem("ehr_patient_portal_brand_location_id")).toBe("9");
    expect(JSON.parse(window.sessionStorage.getItem("ehr_patient_portal_accepted_scopes"))).toEqual([
      "patient/Patient.read",
      "patient/Condition.read",
    ]);
  });

  test("announces the result count while typing, and nothing before the patient types", async () => {
    renderPickerPage();
    window.storeToken("tok");
    const rows = [{ id: 1, brandName: "A" }, { id: 2, brandName: "B" }];
    global.fetch = jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ results: rows }) }));
    await window.pfClient.connect({ id: 5, name: "EHR Patient Portal" });
    const status = document.getElementById("hospital-results-status");
    expect(status.getAttribute("role")).toBe("status");
    expect(status.textContent).toBe("");

    const input = document.getElementById("hospital-search");
    input.value = "mad";
    input.dispatchEvent(new Event("input"));
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(status.textContent).toBe("2 results");

    rows.splice(1);
    input.dispatchEvent(new Event("input"));
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(status.textContent).toBe("1 result");

    rows.splice(0);
    input.dispatchEvent(new Event("input"));
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(status.textContent).toBe("No hospitals found. Try a different name, city, or state.");
  });

  test("ignores a slower earlier search response so the list and count match the current query", async () => {
    renderPickerPage();
    window.storeToken("tok");
    const pending = {};
    global.fetch = jest.fn((url) => new Promise((resolve) => {
      const q = new URL(url).searchParams.get("q");
      pending[q] = (rows) => resolve({ ok: true, json: () => Promise.resolve({ results: rows }) });
      if (q === "") pending[q]([]);
    }));
    await window.pfClient.connect({ id: 5, name: "EHR Patient Portal" });
    const input = document.getElementById("hospital-search");
    input.value = "ma";
    input.dispatchEvent(new Event("input"));
    await new Promise((resolve) => setTimeout(resolve, 250));
    input.value = "mad";
    input.dispatchEvent(new Event("input"));
    await new Promise((resolve) => setTimeout(resolve, 250));

    pending.mad([{ id: 1, brandName: "Madison" }]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    pending.ma([{ id: 2, brandName: "A" }, { id: 3, brandName: "B" }]);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(document.querySelectorAll("#hospital-results [data-brand-result]")).toHaveLength(1);
    expect(document.getElementById("hospital-results-status").textContent).toBe("1 result");
  });

  test("unchecking a scope before continuing excludes it from the authorize request", async () => {
    renderPickerPage();
    window.storeToken("tok");
    global.fetch = jest.fn(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            results: [
              {
                id: 9,
                brandName: "Epic Sandbox",
                fhirBaseUrl: "https://epic/FHIR/R4",
                ehrClientId: "brand-cid",
                supportedScopes: "patient/Patient.read patient/Condition.read",
              },
            ],
          }),
      }),
    );
    await window.pfClient.connect({ id: 5, name: "EHR Patient Portal" });
    const main = document.getElementById("pf_main");
    const authorize = jest.fn();
    global.FHIR = { oauth2: { authorize } };
    main.querySelector("#hospital-results [data-brand-result]").click();

    main.querySelectorAll("#scope-picker-list input[type=checkbox]")[1].click();
    document.getElementById("scope-picker-continue").click();

    expect(authorize.mock.calls[0][0].scope).toBe("openid profile launch/patient patient/Patient.read");
  });

  test("stores the data source being connected, so the callback registers records against that source", async () => {
    // A client can be linked to more than one data source; the callback page has no route params to read it back from.
    global.PATIENT_FACING_CONFIG.dataSourceIds = [5, 6];
    renderPickerPage();
    window.storeToken("tok");
    global.fetch = jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ results: [] }) }));

    await window.pfClient.connect({ id: 6, name: "EHR Patient Portal" });

    expect(window.sessionStorage.getItem("ehr_patient_portal_source_id")).toBe("6");
  });

  test("typing is debounced into one search", async () => {
    renderPickerPage();
    window.storeToken("tok");
    global.fetch = jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ results: [] }) }));
    await window.pfClient.connect({ id: 5, name: "EHR Patient Portal" });
    const initial = global.fetch.mock.calls.length;

    jest.useFakeTimers();
    const input = document.getElementById("hospital-search");
    input.value = "sinai";
    input.dispatchEvent(new Event("input"));
    input.dispatchEvent(new Event("input"));
    jest.advanceTimersByTime(200);
    jest.useRealTimers();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(global.fetch.mock.calls).toHaveLength(initial + 1);
    expect(global.fetch.mock.calls[initial][0]).toContain("q=sinai");
  });
});
