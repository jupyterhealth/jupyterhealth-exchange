import { describe, test, expect, beforeAll, beforeEach, jest } from "@jest/globals";

// client-patient-access.js exposes paPullResourceType + paWriteResource on window.
beforeAll(() => {
  require("../../../core/static/clients/patient-access/js/client-patient-access.js");
});

// Epic serves R4; JHE validates R5. Writes must go through the /fhir-import/R4/ endpoint,
// which converts R4->R5 and returns a batch-response Bundle whose single entry carries the
// real create status. A 200 HTTP response can still contain a per-entry 400.
function importResponse(entryStatus) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ resourceType: "Bundle", type: "batch-response", entry: [{ response: { status: entryStatus } }] }),
  });
}

beforeEach(() => {
  global.fetch = jest.fn(() => importResponse("201 Created"));
});

// patient.request = compartment searches; request = plain instance reads (used for Patient).
function fakeClient(items) {
  return {
    patient: { id: "epic-1", request: jest.fn(() => Promise.resolve(items)) },
    request: jest.fn(() => Promise.resolve(items)),
  };
}

const CONDITION_PULL = { label: "Conditions", type: "Condition", query: "Condition" };

describe("paWriteResource", () => {
  test("POSTs to the R4 import endpoint (not the R5 endpoint)", async () => {
    await window.paWriteResource("tok", "1", "Condition", { resourceType: "Condition" });
    const url = global.fetch.mock.calls[0][0];
    expect(url).toContain("/fhir-import/R4/Condition");
    expect(url).not.toContain("/FHIR/R5/");
  });

  test("returns true when the import entry status is 2xx", async () => {
    global.fetch = jest.fn(() => importResponse("201 Created"));
    expect(await window.paWriteResource("tok", "1", "Condition", {})).toBe(true);
  });

  test("returns false when the import entry status is 4xx (conversion/validation failed)", async () => {
    global.fetch = jest.fn(() => importResponse("400 invalid"));
    expect(await window.paWriteResource("tok", "1", "MedicationRequest", {})).toBe(false);
  });
});

describe("paPullResourceType", () => {
  test("writes each matching resource and counts them", async () => {
    const client = fakeClient([{ resourceType: "Condition" }, { resourceType: "Condition" }]);
    const r = await window.paPullResourceType(client, "tok", "1", CONDITION_PULL, "iss");
    expect(r).toEqual({ written: 2, failed: 0, error: null });
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  test("skips resources of a different type", async () => {
    const client = fakeClient([{ resourceType: "Condition" }, { resourceType: "Observation" }]);
    const r = await window.paPullResourceType(client, "tok", "1", CONDITION_PULL, "iss");
    expect(r.written).toBe(1);
  });

  test("a pull failure is isolated, not thrown", async () => {
    const client = { patient: { id: "epic-1", request: jest.fn(() => Promise.reject(new Error("timeout"))) } };
    const r = await window.paPullResourceType(client, "tok", "1", CONDITION_PULL, "iss");
    expect(r).toEqual({ written: 0, failed: 0, error: "timeout" });
  });

  test("a per-entry import error counts as failed, not written", async () => {
    global.fetch = jest.fn(() => importResponse("400 invalid"));
    const client = fakeClient([{ resourceType: "Condition" }]);
    const r = await window.paPullResourceType(client, "tok", "1", CONDITION_PULL, "iss");
    expect(r).toEqual({ written: 0, failed: 1, error: null });
  });

  test("single read uses a plain instance read (not a patient-compartment search)", async () => {
    const client = fakeClient({ resourceType: "Patient", id: "epic-1" });
    const pull = { label: "Demographics", type: "Patient", query: "Patient", single: true };
    const r = await window.paPullResourceType(client, "tok", "1", pull, "iss");
    expect(r.written).toBe(1);
    expect(client.request).toHaveBeenCalledWith("Patient/epic-1");
    expect(client.patient.request).not.toHaveBeenCalled();
  });
});

describe("paSavePatientIdentifier", () => {
  test("POSTs to the hyphenated route registered in core/urls.py", async () => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: true }));

    await window.paSavePatientIdentifier("tok", "https://sinai/FHIR/R4", "epic-1");

    const [url, opts] = global.fetch.mock.calls[0];
    // An underscore here 404s, so the callback aborts with "failed to store patient id".
    expect(url).toContain("/api/v1/patient-access/identifier");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ system: "https://sinai/FHIR/R4", value: "epic-1" });
  });
});

describe("finishPatientAccessConnect", () => {
  const PICKED = "https://mercy.example.org/FHIR/R4";
  // A configured default that is NOT the hospital the patient picked; provenance must
  // never fall back to it, otherwise multi-hospital records are labelled with the wrong iss.
  const CONFIG = { iss: "https://seeded-default.example.org/FHIR/R4", dataSourceId: 3 };

  function jsonOk(body) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }

  beforeEach(() => {
    sessionStorage.setItem("patient_access_jhe_access_token", "tok");
    // No records to pull, so the run stops after identifier + FhirSource registration.
    const client = {
      state: { serverUrl: PICKED },
      patient: { id: "epic-1", request: jest.fn(() => Promise.resolve([])) },
      request: jest.fn(() => Promise.resolve(null)),
    };
    global.FHIR = { oauth2: { ready: jest.fn(() => Promise.resolve(client)) } };
    global.fetch = jest.fn((url) => (String(url).includes("fhir_sources") ? jsonOk({ id: 9 }) : jsonOk({})));
  });

  test("stamps the authorized server URL - not the configured default - on the identifier and FhirSource", async () => {
    const out = { textContent: "" };

    await window.finishPatientAccessConnect(out, CONFIG);

    const calls = global.fetch.mock.calls;
    const identifier = calls.find(([url]) => String(url).includes("patient-access/identifier"));
    const source = calls.find(([url]) => String(url).includes("fhir_sources"));

    expect(JSON.parse(identifier[1].body).system).toBe(PICKED);
    expect(JSON.parse(source[1].body).fhir_base_url).toBe(PICKED);
    expect(JSON.stringify(calls)).not.toContain(CONFIG.iss);
  });

  test("stops with an error when the authorization carries no server URL", async () => {
    global.FHIR.oauth2.ready = jest.fn(() => Promise.resolve({ state: {}, patient: { id: "epic-1" } }));
    const out = { textContent: "" };

    await window.finishPatientAccessConnect(out, CONFIG);

    expect(out.textContent).toContain("no FHIR server URL");
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
