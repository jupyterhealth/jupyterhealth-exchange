import { describe, test, expect, beforeAll, beforeEach, jest } from "@jest/globals";

// Loads client-patient-access.js which exposes the picker helpers on window.
beforeAll(() => {
  require("../../../core/static/clients/patient-access/js/client-patient-access.js");
});

beforeEach(() => {
  global.fetch = jest.fn();
  delete global.FHIR;
});

describe("paSearchBrands", () => {
  test("queries the brands API with the JHE token and returns the results", async () => {
    const rows = [{ brandName: "Mount Sinai", facilityName: "MSH", fhirBaseUrl: "https://s/FHIR/R4", addressText: "NY" }];
    global.fetch = jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ results: rows }) }));

    const out = await window.paSearchBrands("tok", "sinai");

    expect(out).toEqual(rows);
    const [url, opts] = global.fetch.mock.calls[0];
    // Hyphen, matching the route in core/urls.py. An underscore 404s and paSearchBrands
    // swallows it into [], so the picker just says "no hospitals found".
    expect(url).toContain("/api/v1/patient-access/brands");
    expect(url).toContain("q=sinai");
    expect(opts.headers.Authorization).toBe("Bearer tok");
  });

  test("returns [] on a failed response", async () => {
    global.fetch = jest.fn(() => Promise.resolve({ ok: false }));
    const out = await window.paSearchBrands("tok", "x");
    expect(out).toEqual([]);
  });
});

describe("paAuthorizeWithIss", () => {
  test("launches SMART authorize with the selected hospital's iss", () => {
    const authorize = jest.fn();
    global.FHIR = { oauth2: { authorize } };
    const config = { clientId: "cid", scope: "launch/patient" };

    window.paAuthorizeWithIss(config, "https://sinai/FHIR/R4");

    expect(authorize).toHaveBeenCalledTimes(1);
    const arg = authorize.mock.calls[0][0];
    expect(arg.iss).toBe("https://sinai/FHIR/R4");
    expect(arg.clientId).toBe("cid");
    expect(arg.scope).toBe("launch/patient");
    expect(arg.redirectUri).toContain("/clients/patient-access/callback");
  });
});

describe("paRenderBrandResults", () => {
  test("renders a clickable row per result and fires onSelect with that result", () => {
    const container = document.createElement("div");
    const rows = [
      { brandName: "Mount Sinai", facilityName: "MSH", fhirBaseUrl: "https://a/FHIR/R4", addressText: "1 Levy Pl" },
      { brandName: "Mercy", facilityName: "Mercy STL", fhirBaseUrl: "https://b/FHIR/R4", addressText: "MO" },
    ];
    const onSelect = jest.fn();

    const n = window.paRenderBrandResults(container, rows, onSelect);

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
    const n = window.paRenderBrandResults(container, [], jest.fn());
    expect(n).toBe(0);
    expect(container.textContent.toLowerCase()).toContain("no ");
  });
});
