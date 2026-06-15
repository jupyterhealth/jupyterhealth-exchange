import { describe, test, expect, beforeAll, beforeEach } from "@jest/globals";

// client-jhe-admin.js attaches the patient-form validation helpers to window
// (see GH #571). Requiring it only defines those helpers; no SPA bootstrap runs.
beforeAll(() => {
  require("../../../core/static/clients/jhe-admin/js/client-jhe-admin.js");
});

// Build the email input and identifier rows the helpers read from the DOM.
function setupPatientForm({ email, identifiers = [] } = {}) {
  const rows = identifiers
    .map(
      (row) => `
      <div class="patient-identifier-row">
        <input class="patient-identifier-system" value="${row.system ?? ""}" />
        <input class="patient-identifier-value" value="${row.value ?? ""}" />
      </div>`
    )
    .join("");
  document.body.innerHTML = `
    ${
      email === undefined
        ? ""
        : `<input id="patientTelecomEmail" value="${email}" />`
    }
    <div id="patientIdentifiersContainer">${rows}</div>
  `;
}

describe("isValidPatientEmail", () => {
  test("accepts a valid address", () => {
    expect(window.isValidPatientEmail("user@example.com")).toBe(true);
  });

  test("rejects an empty string", () => {
    expect(window.isValidPatientEmail("")).toBe(false);
  });

  test("rejects a malformed address", () => {
    expect(window.isValidPatientEmail("user@example")).toBe(false);
    expect(window.isValidPatientEmail("userexample.com")).toBe(false);
  });

  test("rejects an address with whitespace", () => {
    expect(window.isValidPatientEmail("user @example.com")).toBe(false);
    expect(window.isValidPatientEmail(" user@example.com ")).toBe(false);
  });
});

describe("validatePatientForm", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  test("requires an e-mail when checkEmail is true", () => {
    setupPatientForm({ email: "" });
    expect(window.validatePatientForm({ checkEmail: true })).toContain(
      "Patient e-mail is required."
    );
  });

  test("flags an invalid e-mail when checkEmail is true", () => {
    setupPatientForm({ email: "not-an-email" });
    expect(window.validatePatientForm({ checkEmail: true })).toContain(
      "Patient e-mail is not a valid e-mail address."
    );
  });

  test("skips the e-mail check when checkEmail is false", () => {
    setupPatientForm({ email: "" });
    expect(window.validatePatientForm({ checkEmail: false })).toEqual([]);
  });

  test("flags a half-filled identifier row", () => {
    setupPatientForm({
      email: "user@example.com",
      identifiers: [{ system: "mrn", value: "" }],
    });
    expect(window.validatePatientForm({ checkEmail: true })).toContain(
      "Each external identifier needs both a System and a Value."
    );
  });

  test("passes with a valid e-mail and complete identifier rows", () => {
    setupPatientForm({
      email: "user@example.com",
      identifiers: [{ system: "mrn", value: "123" }],
    });
    expect(window.validatePatientForm({ checkEmail: true })).toEqual([]);
  });
});

describe("collectPatientIdentifiers", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  test("returns only fully-filled rows", () => {
    setupPatientForm({
      identifiers: [
        { system: "mrn", value: "123" },
        { system: "mrn", value: "" },
        { system: "", value: "456" },
      ],
    });
    expect(window.collectPatientIdentifiers()).toEqual([
      { system: "mrn", value: "123" },
    ]);
  });

  test("trims surrounding whitespace from values", () => {
    setupPatientForm({
      identifiers: [{ system: "  mrn  ", value: "  123  " }],
    });
    expect(window.collectPatientIdentifiers()).toEqual([
      { system: "mrn", value: "123" },
    ]);
  });

  test("returns an empty array when there are no rows", () => {
    setupPatientForm({ identifiers: [] });
    expect(window.collectPatientIdentifiers()).toEqual([]);
  });
});
