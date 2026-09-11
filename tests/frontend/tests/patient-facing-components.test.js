import { describe, test, expect, beforeAll } from "@jest/globals";
const fs = require("fs");
const path = require("path");

const STATIC = path.resolve(__dirname, "../../../core/static");
const TEMPLATES = path.resolve(__dirname, "../../../core/templates");
const COMPONENTS = [
  "common/patient_facing/components/hub.html",
  "common/patient_facing/components/consent.html",
  "common/patient_facing/components/done.html",
  "common/patient_facing/components/manage.html",
  "common/patient_facing/components/importing.html",
  "common/patient_facing/components/receipt.html",
  "common/patient_facing/components/rail.html",
  "common/patient_facing/components/error.html",
];

function componentHtml(file) {
  return fs.readFileSync(path.join(TEMPLATES, file), "utf8").replace(/{% ?verbatim ?%}|{% ?endverbatim ?%}/g, "");
}

const RECEIPT = { synced: [{ label: "Labs", n: 12 }], notSynced: [{ label: "Allergies", n: 0 }], total: 12 };

beforeAll(() => {
  global.Handlebars = require(path.join(STATIC, "common/js/handlebars.min.js"));
  require(path.join(STATIC, "common/js/common.js"));
  require(path.join(STATIC, "common/js/patient-facing.js"));
  global.PATIENT_PORTAL_CONFIG = { pageUrl: "/clients/ow/launch", siteTitle: "JupyterHealth Exchange" };
  document.body.innerHTML = `<div id="pf_main"></div>` + COMPONENTS.map(componentHtml).join("");
  window.pfRegisterPartials();
});

function render(id, context) {
  window.pfRender(id, context);
  return document.getElementById("pf_main");
}

test("hub lists a card per source with its badge, or the empty callout", () => {
  const main = render("t-hub", {
    eyebrow: "Lifespan Study on Sleep & BP",
    cards: [
      { id: 3, title: "Oura", desc: "Sleep episode", icon: "bi-smartwatch", on: true, badge: "Consented", route: "manage" },
      { id: 5, title: "EHR Patient Portal", desc: "Clinical records", icon: "bi-file-earmark-text", on: false, badge: "Not consented", route: "consent" },
    ],
  });
  expect(main.querySelector(".pf-eyebrow").textContent).toBe("Lifespan Study on Sleep & BP");
  expect(main.querySelector(".pf-h1").textContent).toBe("Choose how to share your data");
  const cards = main.querySelectorAll("a.pf-card-link");
  expect(cards).toHaveLength(2);
  expect(cards[0].getAttribute("onclick")).toContain("pfNav('manage', { source: '3' })");
  expect(cards[0].querySelector(".pf-card__badge").className).toBe("pf-card__badge pf-card__badge--on");
  expect(cards[1].querySelector(".pf-card__badge").className).toBe("pf-card__badge");
  expect(cards[1].querySelector(".pf-card__badge").textContent).toBe("Not consented");
  expect(main.querySelector(".pf-back")).toBeNull();

  render("t-hub", { eyebrow: "Your studies", cards: [] });
  expect(document.querySelector("#pf_main .pf-callout").textContent).toContain("hasn't asked for any data yet");
});

test("consent lists the pending scopes with the optional detail and the agree button", () => {
  const main = render("t-consent", { sourceId: 5, eyebrow: "EHR Patient Portal · Lifespan Study on BP & HR", sourceName: "EHR Patient Portal", rows: ["Clinical records"], scopeDetail: "Demographics, labs" });
  expect(main.querySelector(".pf-back").getAttribute("onclick")).toContain("pfNav('hub')");
  expect(main.querySelector(".pf-lede").textContent).toContain("from your EHR Patient Portal while you're enrolled");
  expect(main.querySelector(".pf-card__title").textContent).toBe("Clinical records");
  expect(main.querySelector(".pf-card__desc").textContent).toBe("Demographics, labs");
  expect(main.querySelector(".pf-btn--wide").getAttribute("onclick")).toBe("pfAgree('5')");

  render("t-consent", { sourceId: 3, eyebrow: "Oura", sourceName: "Oura", rows: ["Sleep episode"], scopeDetail: "" });
  expect(document.querySelector("#pf_main .pf-card__desc")).toBeNull();
});

test("done shows the check circle, the consented source and the receipt", () => {
  const main = render("t-done", { lede: "You've agreed to share.", rows: [{ name: "Oura", detail: "Sleep episode" }], receipt: RECEIPT });
  expect(main.querySelector(".pf-check-circle")).not.toBeNull();
  expect(main.querySelector(".pf-consent-row__label").textContent).toBe("Oura · Sleep episode");
  expect(main.querySelector(".pf-receipt__row--total .pf-receipt__n").textContent).toBe("12");
  expect(main.querySelector(".pf-receipt__row--pending").textContent).toContain("Allergies");
  expect(main.querySelector("a.pf-btn").textContent).toBe("Manage sharing");

  render("t-done", { lede: "Nothing is shared yet.", rows: [], receipt: null });
  expect(document.querySelector("#pf_main .pf-receipt")).toBeNull();
});

test("manage shows the facility card or the scope rows, and the stop button", () => {
  const main = render("t-manage", { sourceId: 5, sourceName: "EHR Patient Portal", icon: "bi-file-earmark-text", detail: "Epic Sandbox · Clinical records · 3 records", rows: ["Clinical records"], receipt: RECEIPT });
  expect(main.querySelectorAll(".pf-card")).toHaveLength(1);
  expect(main.querySelector(".pf-card__desc").textContent).toBe("Epic Sandbox · Clinical records · 3 records");
  expect(main.querySelector(".pf-receipt")).not.toBeNull();
  expect(main.querySelector(".pf-btn--danger").getAttribute("onclick")).toBe("pfStopSharing('5')");

  render("t-manage", { sourceId: 3, sourceName: "Oura", icon: "bi-smartwatch", detail: null, rows: ["Heart Rate", "Sleep episode"], receipt: null });
  const titles = Array.from(document.querySelectorAll("#pf_main .pf-card__title")).map((el) => el.textContent);
  expect(titles).toEqual(["Heart Rate", "Sleep episode"]);
});

test("importing renders the rail at step 3, the hidden log and the progress card with no exits", () => {
  const main = render("t-importing", { rail: { steps: [{ num: 1, label: "Choose organization", cls: " is-done" }, { num: 2, label: "Sign in", cls: " is-done" }, { num: 3, label: "Import records", cls: " is-active" }] } });
  const steps = main.querySelectorAll(".pf-rail__step");
  expect(steps).toHaveLength(3);
  expect(steps[2].className).toBe("pf-rail__step is-active");
  expect(main.querySelector(".pf-import #out")).not.toBeNull();
  expect(main.querySelector(".pf-progress__bar")).not.toBeNull();
  expect(main.querySelector(".pf-back")).toBeNull();
  expect(main.textContent).not.toContain("View summary");
});
