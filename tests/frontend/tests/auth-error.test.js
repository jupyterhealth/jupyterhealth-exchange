import { describe, test, expect, beforeAll } from "@jest/globals";

// oidc.js attaches the OAuth error-relay helpers to window (see GH #192).
// Importing it only defines window.initOidc and the helpers; initOidc is not
// invoked here, so no oidc-client-ts dependency is required.
beforeAll(() => {
  require("../../../core/static/common/js/oidc.js");
});

describe("describeOidcError", () => {
  test("uses error_description when present", () => {
    expect(
      window.describeOidcError({
        error: "server_error",
        error_description: "invalid private key",
      })
    ).toBe("invalid private key");
  });

  test("falls back to the error code", () => {
    expect(window.describeOidcError({ error: "server_error" })).toBe(
      "server_error"
    );
  });

  test("uses message for a generic Error", () => {
    expect(window.describeOidcError(new Error("boom"))).toBe("boom");
  });

  test("passes a plain string through", () => {
    expect(window.describeOidcError("oops")).toBe("oops");
  });

  test("falls back to a generic message when empty", () => {
    expect(window.describeOidcError(null)).toBe(
      "Sign-in failed. Please try again."
    );
  });
});
