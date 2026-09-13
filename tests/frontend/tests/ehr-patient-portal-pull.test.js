import { describe, test, expect, beforeAll, beforeEach, jest } from "@jest/globals";
const fs = require("fs");
const path = require("path");

const STATIC = path.resolve(__dirname, "../../../core/static");

// client-ehr-patient-portal.js references getStoredToken/pfRender from patient-facing.js, and
// exposes eppPullResourceType + eppWriteResource on window.
beforeAll(() => {
  global.Handlebars = require(path.join(STATIC, "common/js/handlebars.min.js"));
  require(path.join(STATIC, "common/js/common.js"));
  require(path.join(STATIC, "common/js/patient-facing.js"));
  require("../../../core/static/clients/ehr-patient-portal/js/client-ehr-patient-portal.js");
});

// Epic serves R4; JHE validates R5. Writes must go through the /fhir-import/R4/ endpoint,
// which converts R4->R5 and returns a batch-response Bundle whose single entry carries the
// real create status. A 200 HTTP response can still contain a per-entry 400.
function importResponse(entryStatus, outcome) {
  return Promise.resolve({
    ok: true,
    json: () =>
      Promise.resolve({
        resourceType: "Bundle",
        type: "batch-response",
        entry: [{ response: { status: entryStatus, outcome: outcome } }],
      }),
  });
}

// The endpoint's failure outcome: the create-path error as an OperationOutcome issue.
function errorOutcome(diagnostics) {
  return { resourceType: "OperationOutcome", issue: [{ severity: "error", code: "invalid", diagnostics: diagnostics }] };
}

// eppPullResourceType posts chunked batch Bundles: reply with one entry per posted resource,
// mirroring the endpoint's order-aligned batch-response contract.
function batchFetchMock(entryStatus, outcome) {
  return jest.fn((url, opts) => {
    const posted = JSON.parse(opts.body);
    const count = posted.resourceType === "Bundle" ? posted.entry.length : 1;
    return Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve({
          resourceType: "Bundle",
          type: "batch-response",
          entry: Array.from({ length: count }, () => ({ response: { status: entryStatus, outcome: outcome } })),
        }),
    });
  });
}

beforeEach(() => {
  global.fetch = batchFetchMock("201 Created");
});

// patient.request = compartment searches; request = plain instance reads (used for Patient).
function fakeClient(items) {
  return {
    patient: { id: "epic-1", request: jest.fn(() => Promise.resolve(items)) },
    request: jest.fn(() => Promise.resolve(items)),
  };
}

const CONDITION_PULL = { label: "Conditions", type: "Condition", query: "Condition" };

describe("eppWriteResource", () => {
  test("POSTs to the R4 import endpoint (not the R5 endpoint)", async () => {
    await window.eppWriteResource("tok", "1", "Condition", { resourceType: "Condition" });
    const url = global.fetch.mock.calls[0][0];
    expect(url).toContain("/fhir-import/R4/Condition");
    expect(url).not.toContain("/FHIR/R5/");
  });

  test("reports ok when the import entry status is 2xx", async () => {
    global.fetch = jest.fn(() => importResponse("201 Created"));
    expect(await window.eppWriteResource("tok", "1", "Condition", {})).toEqual({ ok: true, reason: null, warnings: [] });
  });

  test("reports the entry's OperationOutcome error when the status is 4xx", async () => {
    global.fetch = jest.fn(() => importResponse("400 invalid", errorOutcome("clinicalStatus: field required")));
    expect(await window.eppWriteResource("tok", "1", "MedicationRequest", {})).toEqual({
      ok: false,
      reason: "clinicalStatus: field required",
      warnings: [],
    });
  });

  test("falls back to the entry status when the outcome carries no error issue", async () => {
    global.fetch = jest.fn(() => importResponse("400 invalid"));
    expect(await window.eppWriteResource("tok", "1", "MedicationRequest", {})).toEqual({
      ok: false,
      reason: "400 invalid",
      warnings: [],
    });
  });

  test("keeps the response body in the reason for a transport-level failure", async () => {
    // A scope rejection must not read as a bare 403 — the body says which scope.
    global.fetch = jest.fn(() =>
      Promise.resolve({
        ok: false,
        status: 403,
        text: () => Promise.resolve('{"detail":"missing patient/Condition.read scope"}'),
      }),
    );
    expect(await window.eppWriteResource("tok", "1", "Condition", {})).toEqual({
      ok: false,
      reason: 'HTTP 403: {"detail":"missing patient/Condition.read scope"}',
      warnings: [],
    });
  });
});

describe("eppWriteBundle", () => {
  test("POSTs one batch Bundle and maps the response entries back in order", async () => {
    global.fetch = jest.fn((url, opts) =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            resourceType: "Bundle",
            type: "batch-response",
            entry: [
              { response: { status: "201 Created" } },
              { response: { status: "400 invalid", outcome: errorOutcome("boom") } },
            ],
          }),
      }),
    );
    const writes = await window.eppWriteBundle("tok", "1", [{ resourceType: "Condition" }, { resourceType: "Condition" }]);
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toMatch(/\/fhir-import\/R4\/$/); // the bundle route, no resource type suffix
    expect(JSON.parse(opts.body).entry).toHaveLength(2);
    expect(writes).toEqual([
      { ok: true, reason: null, warnings: [] },
      { ok: false, reason: "boom", warnings: [] },
    ]);
  });

  test("replicates a transport failure across every posted resource", async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({ ok: false, status: 403, text: () => Promise.resolve('{"detail":"nope"}') }),
    );
    const writes = await window.eppWriteBundle("tok", "1", [{}, {}]);
    expect(writes).toHaveLength(2);
    expect(writes[0]).toEqual({ ok: false, reason: 'HTTP 403: {"detail":"nope"}', warnings: [] });
    expect(writes[1]).toEqual(writes[0]);
  });

  test("a rejected fetch is reported per resource, never thrown (pull isolation)", async () => {
    // A network blip / worker timeout mid-chunk must not abort the whole multi-type pull.
    global.fetch = jest.fn(() => Promise.reject(new TypeError("Failed to fetch")));
    const writes = await window.eppWriteBundle("tok", "1", [{}, {}]);
    expect(writes).toEqual([
      { ok: false, reason: "network error: Failed to fetch", warnings: [] },
      { ok: false, reason: "network error: Failed to fetch", warnings: [] },
    ]);

    // Same for a truncated 200 body (response.json() rejects).
    global.fetch = jest.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.reject(new SyntaxError("Unexpected end of JSON input")) }),
    );
    const writes2 = await window.eppWriteBundle("tok", "1", [{}]);
    expect(writes2[0].ok).toBe(false);
    expect(writes2[0].reason).toContain("Unexpected end of JSON input");
  });
});

describe("eppPullResourceType", () => {
  test("writes each matching resource and counts them", async () => {
    const client = fakeClient([{ resourceType: "Condition" }, { resourceType: "Condition" }]);
    const r = await window.eppPullResourceType(client, "tok", "1", CONDITION_PULL, "iss");
    expect(r).toEqual({ written: 2, failed: 0, error: null, reasons: {}, warnings: {} });
    // Batched: both records travel in ONE Bundle POST, not one round trip per record.
    expect(global.fetch).toHaveBeenCalledTimes(1);
    const posted = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(posted.resourceType).toBe("Bundle");
    expect(posted.entry).toHaveLength(2);
  });

  test("skips resources of a different type", async () => {
    const client = fakeClient([{ resourceType: "Condition" }, { resourceType: "Observation" }]);
    const r = await window.eppPullResourceType(client, "tok", "1", CONDITION_PULL, "iss");
    expect(r.written).toBe(1);
  });

  test("a pull failure is isolated, not thrown", async () => {
    const client = { patient: { id: "epic-1", request: jest.fn(() => Promise.reject(new Error("timeout"))) } };
    const r = await window.eppPullResourceType(client, "tok", "1", CONDITION_PULL, "iss");
    expect(r).toEqual({ written: 0, failed: 0, error: "timeout", reasons: {}, warnings: {} });
  });

  test("a per-entry import error counts as failed and aggregates the distinct reason", async () => {
    global.fetch = batchFetchMock("400 invalid", errorOutcome("clinicalStatus: field required"));
    const client = fakeClient([{ resourceType: "Condition" }, { resourceType: "Condition" }]);
    const r = await window.eppPullResourceType(client, "tok", "1", CONDITION_PULL, "iss");
    expect(r).toEqual({
      written: 0,
      failed: 2,
      error: null,
      reasons: { "clinicalStatus: field required": 2 },
      warnings: {},
    });
  });

  test("a successful entry's warnings are surfaced and aggregated", async () => {
    // e.g. Conditions imported with clinicalStatus defaulted to 'unknown' — saved, but changed.
    const warned = {
      resourceType: "OperationOutcome",
      issue: [{ severity: "warning", code: "informational", diagnostics: "clinicalStatus defaulted to 'unknown'." }],
    };
    global.fetch = batchFetchMock("201 Created", warned);
    const client = fakeClient([{ resourceType: "Condition" }, { resourceType: "Condition" }]);
    const r = await window.eppPullResourceType(client, "tok", "1", CONDITION_PULL, "iss");
    expect(r).toEqual({
      written: 2,
      failed: 0,
      error: null,
      reasons: {},
      warnings: { "clinicalStatus defaulted to 'unknown'.": 2 },
    });
  });

  test("a record is marked seen only after a successful write, so a later pull retries it", async () => {
    const seen = new Set();
    const labs = { label: "Labs", type: "Observation", query: "Observation?category=laboratory" };
    const vitals = { label: "Vital Signs", type: "Observation", query: "Observation?category=vital-signs" };
    const obs = { resourceType: "Observation", id: "obs-1" };

    // First pull: the write fails -> obs-1 must NOT be marked seen.
    global.fetch = batchFetchMock("400 invalid", errorOutcome("boom"));
    let r = await window.eppPullResourceType(fakeClient([obs]), "tok", "1", labs, "iss", seen);
    expect(r.failed).toBe(1);
    expect(seen.has("obs-1")).toBe(false);

    // Second pull returns the same record: retried, succeeds, and only then is it seen.
    global.fetch = batchFetchMock("201 Created");
    r = await window.eppPullResourceType(fakeClient([obs]), "tok", "1", vitals, "iss", seen);
    expect(r.written).toBe(1);
    expect(seen.has("obs-1")).toBe(true);

    // Third pull: now deduplicated.
    r = await window.eppPullResourceType(fakeClient([obs]), "tok", "1", labs, "iss", seen);
    expect(r.written).toBe(0);
  });

  test("single read uses a plain instance read (not a patient-compartment search)", async () => {
    const client = fakeClient({ resourceType: "Patient", id: "epic-1" });
    const pull = { label: "Demographics", type: "Patient", query: "Patient", single: true };
    const r = await window.eppPullResourceType(client, "tok", "1", pull, "iss");
    expect(r.written).toBe(1);
    expect(client.request).toHaveBeenCalledWith("Patient/epic-1");
    expect(client.patient.request).not.toHaveBeenCalled();
  });
});

describe("eppSavePatientIdentifier", () => {
  test("POSTs to the hyphenated route registered in core/urls.py", async () => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: true }));

    await window.eppSavePatientIdentifier("tok", "https://sinai/FHIR/R4", "epic-1");

    const [url, opts] = global.fetch.mock.calls[0];
    // An underscore here 404s, so the callback aborts with "failed to store patient id".
    expect(url).toContain("/api/v1/ehr-patient-portal/identifier");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ system: "https://sinai/FHIR/R4", value: "epic-1" });
  });
});

describe("finishEhrPatientPortalConnect", () => {
  const PICKED = "https://mercy.example.org/FHIR/R4";
  const CONFIG = { dataSourceIds: [3] };

  function jsonOk(body) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }

  beforeEach(() => {
    window.storeToken("tok");
    sessionStorage.removeItem("ehr_patient_portal_brand_location_id");
    sessionStorage.removeItem("ehr_patient_portal_source_id");
    // No records to pull, so the run stops after identifier + FhirSource registration.
    const client = {
      state: { serverUrl: PICKED },
      patient: { id: "epic-1", request: jest.fn(() => Promise.resolve([])) },
      request: jest.fn(() => Promise.resolve(null)),
    };
    global.FHIR = { oauth2: { ready: jest.fn(() => Promise.resolve(client)) } };
    global.fetch = jest.fn((url) => (String(url).includes("fhir_sources") ? jsonOk({ id: 9 }) : jsonOk({})));
  });

  test("records the picked hospital location on the FhirSource", async () => {
    // The picker stores the row id before the SMART redirect; registration reads it back. The
    // server cannot re-derive it -- iss identifies a brand, and a brand has many locations.
    window.eppStoreBrandLocationId(4242);
    const out = { textContent: "" };

    await window.finishEhrPatientPortalConnect(out, CONFIG);

    const source = global.fetch.mock.calls.find(([url]) => String(url).includes("fhir_sources"));
    expect(JSON.parse(source[1].body)).toEqual({ label: "Epic / EHR Patient Portal — " + PICKED, data_source: 3, ehr_brand_location: 4242 });
  });

  test("omits the location when the patient did not come through the picker", async () => {
    const out = { textContent: "" };

    await window.finishEhrPatientPortalConnect(out, CONFIG);

    const source = global.fetch.mock.calls.find(([url]) => String(url).includes("fhir_sources"));
    expect(JSON.parse(source[1].body)).not.toHaveProperty("ehr_brand_location");
  });

  test("stamps the authorized server URL on the identifier and source label", async () => {
    const out = { textContent: "" };

    await window.finishEhrPatientPortalConnect(out, CONFIG);

    const calls = global.fetch.mock.calls;
    const identifier = calls.find(([url]) => String(url).includes("ehr-patient-portal/identifier"));
    const source = calls.find(([url]) => String(url).includes("fhir_sources"));

    expect(JSON.parse(identifier[1].body).system).toBe(PICKED);
    // A FhirSource holds no endpoint; the label is its only human-facing handle, so the
    // authorized server URL goes there.
    expect(JSON.parse(source[1].body)).toEqual({ label: "Epic / EHR Patient Portal — " + PICKED, data_source: 3 });
  });

  test("registers the data source the patient chose to connect", async () => {
    window.eppStoreSourceId(6);
    const out = { textContent: "" };

    await window.finishEhrPatientPortalConnect(out, CONFIG);

    const source = global.fetch.mock.calls.find(([url]) => String(url).includes("fhir_sources"));
    expect(JSON.parse(source[1].body).data_source).toBe(6);
  });

  test("stops with an error when the authorization carries no server URL", async () => {
    global.FHIR.oauth2.ready = jest.fn(() => Promise.resolve({ state: {}, patient: { id: "epic-1" } }));
    const out = { textContent: "" };

    await window.finishEhrPatientPortalConnect(out, CONFIG);

    expect(out.textContent).toContain("no FHIR server URL");
    expect(global.fetch).not.toHaveBeenCalled();
  });
});

describe("eppCallback", () => {
  const TEMPLATES = path.resolve(__dirname, "../../../core/templates");

  // A Django component file is a {% verbatim %}-wrapped <script type="text/template">; strip the tags to get the HTML.
  function componentHtml(file) {
    return fs.readFileSync(file, "utf8").replace(/{% ?verbatim ?%}|{% ?endverbatim ?%}/g, "");
  }

  beforeEach(() => {
    document.body.innerHTML =
      `<div id="pf_main"></div>` +
      ["common/patient_facing/components/importing.html", "common/patient_facing/components/rail.html", "common/patient_facing/components/error.html"]
        .map((file) => componentHtml(path.join(TEMPLATES, file)))
        .join("");
    global.PATIENT_PORTAL_CONFIG = { dataSourceIds: [5], pageUrl: "/clients/ehr-patient-portal/", siteTitle: "T", expectedResourceTypes: [] };
    window.storeToken("tok");
    sessionStorage.removeItem("ehr_patient_portal_source_id");
  });

  test("a failed Epic handshake ends on the callout, with a retry that restarts the picker", async () => {
    global.FHIR = { oauth2: { ready: jest.fn(() => Promise.reject(new Error("state parameter not found"))) } };

    await window.eppCallback();

    expect(document.querySelector(".pf-error__title").textContent).toBe("We couldn't reach your healthcare organization");
    expect(document.querySelector("#pf_main .pf-btn").getAttribute("href")).toBe("/clients/ehr-patient-portal/?route=connect&source=5");
  });

  test("a run where every record type failed to fetch ends on the callout", async () => {
    const refuse = jest.fn(() => Promise.reject(new Error("HTTP 403")));
    global.FHIR = {
      oauth2: { ready: jest.fn(() => Promise.resolve({ state: { serverUrl: "https://mercy.example.org/FHIR/R4" }, patient: { id: "epic-1", request: refuse }, request: refuse })) },
    };
    global.fetch = jest.fn((url) =>
      Promise.resolve({ ok: true, json: () => Promise.resolve(String(url).includes("fhir_sources") ? { id: 9 } : {}) }),
    );

    await window.eppCallback();

    expect(document.querySelector(".pf-error__title").textContent).toBe("We couldn't reach your healthcare organization");
    expect(document.querySelector(".pf-error__msg").textContent).toBe("none of your record types could be fetched");
  });
});
