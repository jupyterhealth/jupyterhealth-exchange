import { describe, test, expect, beforeAll, beforeEach } from "@jest/globals";

// client-jhe-admin.js attaches the practitioner-form validation helpers to
// window (mirrors the patient-form pattern, see GH #571). Requiring it only
// defines those helpers; no SPA bootstrap runs.
beforeAll(() => {
  require("../../../core/static/clients/jhe-admin/js/client-jhe-admin.js");
});

function setupPractitionerForm({ email, family, given } = {}) {
  document.body.innerHTML = `
    ${
      email === undefined
        ? ""
        : `<input id="practitionerTelecomEmail" value="${email}" />`
    }
    ${
      family === undefined
        ? ""
        : `<input id="practitionerFamilyName" value="${family}" />`
    }
    ${
      given === undefined
        ? ""
        : `<input id="practitionerGivenName" value="${given}" />`
    }
  `;
}

describe("validatePractitionerForm", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  test("requires an e-mail when checkEmail is true", () => {
    setupPractitionerForm({ email: "", family: "Smith", given: "Jane" });
    expect(
      window.validatePractitionerForm({ checkEmail: true })
    ).toContain("Practitioner e-mail is required.");
  });

  test("flags an invalid e-mail when checkEmail is true", () => {
    setupPractitionerForm({
      email: "not-an-email",
      family: "Smith",
      given: "Jane",
    });
    expect(
      window.validatePractitionerForm({ checkEmail: true })
    ).toContain("Practitioner e-mail is not a valid e-mail address.");
  });

  test("skips the e-mail check when checkEmail is false", () => {
    setupPractitionerForm({ email: "", family: "Smith", given: "Jane" });
    expect(window.validatePractitionerForm({ checkEmail: false })).toEqual([]);
  });

  test("requires a family name", () => {
    setupPractitionerForm({ email: "user@example.com", family: "", given: "Jane" });
    expect(
      window.validatePractitionerForm({ checkEmail: true })
    ).toContain("Family name is required.");
  });

  test("requires a given name", () => {
    setupPractitionerForm({ email: "user@example.com", family: "Smith", given: "" });
    expect(
      window.validatePractitionerForm({ checkEmail: true })
    ).toContain("Given name is required.");
  });

  test("passes with a valid e-mail, family, and given name", () => {
    setupPractitionerForm({
      email: "user@example.com",
      family: "Smith",
      given: "Jane",
    });
    expect(window.validatePractitionerForm({ checkEmail: true })).toEqual([]);
  });
});
