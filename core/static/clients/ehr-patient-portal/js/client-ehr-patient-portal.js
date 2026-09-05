// ────────────────────────────────────────────────────
// EHR Patient Portal Client - SMART on FHIR patient EHR-records flow.
// Registers the connect step (hospital picker) and the callback entry point for the shared
// patient-facing app (core/static/common/js/patient-facing.js).
// Browser-side: JHE token -> Epic PKCE -> pull USCDI records -> write to JHE.
// Uses SMART fhir-client.js (FHIR.oauth2.*). API_ENDPOINT comes from patient-facing.js.
// ────────────────────────────────────────────────────

// Epic serves R4; JHE validates R5 -- writes go through the R4 import endpoint (cross_version engine, R4->R5) then the normal create, returning a batch-response Bundle.
var IMPORT_ENDPOINT = window.location.origin + "/fhir-import/R4/";
// The picked hospital row is chosen before the SMART redirect and needed after it; the server cannot re-derive it since iss identifies a brand, and a brand has many locations.
var BRAND_LOCATION_KEY = "ehr_patient_portal_brand_location_id";
// Which of this client's data sources the patient is connecting; the callback page has no route params to read it from.
var SOURCE_ID_KEY = "ehr_patient_portal_source_id";

function eppStoreBrandLocationId(id) {
  if (id === undefined || id === null) return;
  sessionStorage.setItem(BRAND_LOCATION_KEY, String(id));
}

function eppGetBrandLocationId() {
  return sessionStorage.getItem(BRAND_LOCATION_KEY);
}

function eppStoreSourceId(id) {
  sessionStorage.setItem(SOURCE_ID_KEY, String(id));
}

function eppGetSourceId() {
  return sessionStorage.getItem(SOURCE_ID_KEY);
}

// Attach the Epic patient id to the JHE patient (additive). Returns true on success.
async function eppSavePatientIdentifier(jheToken, system, value) {
  var response = await fetch(API_ENDPOINT + "ehr-patient-portal/identifier", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + jheToken,
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
    },
    body: JSON.stringify({ system: system, value: value }),
  });
  return response.ok;
}

// The create body for a new FhirSource: dataSourceId comes from the page config (resolved server-side from this client's ClientDataSource link, never looked up here); every Connect registers a NEW source since none stores an endpoint to match against, so the endpoint lives only in the label.
function eppFhirSourceBody(fhirBaseUrl, dataSourceId) {
  var body = { label: "Epic / EHR Patient Portal — " + fhirBaseUrl, data_source: Number(dataSourceId) };
  // Only set when the patient reached here through the picker; other launch routes have no facility to record, hence the nullable field.
  var locationId = eppGetBrandLocationId();
  if (locationId) body.ehr_brand_location = Number(locationId);
  return body;
}

async function eppCreateFhirSource(jheToken, fhirBaseUrl, dataSourceId) {
  var response = await fetch(API_ENDPOINT + "fhir_sources", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + jheToken,
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
    },
    body: JSON.stringify(eppFhirSourceBody(fhirBaseUrl, dataSourceId)),
  });
  if (!response.ok) return null;
  var data = await response.json();
  return data.id;
}

// The error text of a failed import entry, from the first error/fatal issue's diagnostics in response.outcome; null when there is none.
function eppEntryFailureReason(entry) {
  var issues = (entry && entry.response && entry.response.outcome && entry.response.outcome.issue) || [];
  for (var i = 0; i < issues.length; i++) {
    if (issues[i].severity === "error" || issues[i].severity === "fatal") {
      return issues[i].diagnostics || (issues[i].details && issues[i].details.text) || issues[i].code || "unknown error";
    }
  }
  return null;
}

// Warning texts on an import entry (dropped/defaulted R4 fields); present on *successful* entries too, since a changed shape must not be silent.
function eppEntryWarnings(entry) {
  var issues = (entry && entry.response && entry.response.outcome && entry.response.outcome.issue) || [];
  var texts = [];
  for (var i = 0; i < issues.length; i++) {
    if (issues[i].severity === "warning") {
      texts.push(issues[i].diagnostics || (issues[i].details && issues[i].details.text) || issues[i].code);
    }
  }
  return texts;
}

// One import entry's {ok, reason, warnings}: ok is the entry's own create status (2xx); reason carries its OperationOutcome error on failure; warnings can be present even on success (e.g. a defaulted clinicalStatus).
function eppEntryWrite(entry) {
  var status = entry && entry.response && entry.response.status;
  var ok = typeof status === "string" && status.charAt(0) === "2";
  return {
    ok: ok,
    reason: ok ? null : eppEntryFailureReason(entry) || status || "unknown error",
    warnings: eppEntryWarnings(entry),
  };
}

// The failure {ok, reason, warnings} for a transport-level (non-200) import response.
async function eppTransportFailure(response) {
  // Keep the response body: a scope rejection reads as a bare 403 without it.
  var detail = "";
  try {
    detail = (await response.text()).slice(0, 300);
  } catch (e) {
    /* body unreadable; status alone will have to do */
  }
  return { ok: false, reason: "HTTP " + response.status + (detail ? ": " + detail : ""), warnings: [] };
}

function eppImportHeaders(jheToken, sourceId) {
  return {
    Authorization: "Bearer " + jheToken,
    "Content-Type": "application/json",
    "X-JHE-FHIR-Source-ID": String(sourceId),
    "Cache-Control": "no-cache",
  };
}

// POST one R4 resource to the JHE R4 import endpoint (converts R4->R5, then creates); it returns HTTP 200 with a batch-response Bundle even when the entry failed, so success is judged per entry (see eppEntryWrite).
async function eppWriteResource(jheToken, sourceId, resourceType, resource) {
  var response = await fetch(IMPORT_ENDPOINT + resourceType, {
    method: "POST",
    headers: eppImportHeaders(jheToken, sourceId),
    body: JSON.stringify(resource),
  });
  if (!response.ok) return eppTransportFailure(response);
  var bundle = await response.json();
  return eppEntryWrite(bundle && bundle.entry && bundle.entry[0]);
}

// POST a batch of R4 resources as ONE Bundle so hundreds of labs don't mean hundreds of round trips; returns one order-aligned {ok, reason, warnings} per posted resource (a transport failure is replicated across all of them).
async function eppWriteBundle(jheToken, sourceId, resources) {
  // Everything that can reject (network drop, worker timeout, truncated JSON) is caught here and reported per resource, since one failed chunk must not abort the whole multi-type pull.
  try {
    var response = await fetch(IMPORT_ENDPOINT, {
      method: "POST",
      headers: eppImportHeaders(jheToken, sourceId),
      body: JSON.stringify({
        resourceType: "Bundle",
        type: "batch",
        entry: resources.map(function (resource) {
          return { resource: resource };
        }),
      }),
    });
    if (!response.ok) {
      var failure = await eppTransportFailure(response);
      return resources.map(function () {
        return failure;
      });
    }
    var bundle = await response.json();
  } catch (e) {
    var reason = "network error: " + (e && e.message ? e.message : String(e));
    return resources.map(function () {
      return { ok: false, reason: reason, warnings: [] };
    });
  }
  var entries = (bundle && bundle.entry) || [];
  return resources.map(function (resource, i) {
    return eppEntryWrite(entries[i]);
  });
}

// Every patient-compartment clinical type JHE can ingest today (each has an R4->R5 StructureMap and an aux_resources entry in fhir_config.json; reference/meta types like Practitioner, Location and Provenance are resolved from citing resources, not pulled); `single` reads one instance, the rest are patient-scoped searches, in display order, with failures isolated per type.
var EHR_PATIENT_PORTAL_PULLS = [
  { label: "Demographics", type: "Patient", query: "Patient", single: true },
  { label: "Conditions", type: "Condition", query: "Condition" },
  { label: "Medications", type: "MedicationRequest", query: "MedicationRequest" },
  { label: "Medication Dispenses", type: "MedicationDispense", query: "MedicationDispense" },
  { label: "Allergies", type: "AllergyIntolerance", query: "AllergyIntolerance" },
  { label: "Immunizations", type: "Immunization", query: "Immunization" },
  { label: "Procedures", type: "Procedure", query: "Procedure" },
  // Epic requires a category (or code) filter on Observation searches, so each pulled category is its own query, mapping to the "Observation - ..." views in the JHE FHIR Resources browser; OMH device data is JHE-native and never pulled from the EHR.
  { label: "Labs", type: "Observation", query: "Observation?category=laboratory" },
  { label: "Vital Signs", type: "Observation", query: "Observation?category=vital-signs" },
  { label: "Diagnostic Reports", type: "DiagnosticReport", query: "DiagnosticReport" },
  { label: "Documents", type: "DocumentReference", query: "DocumentReference?category=clinical-note" },
  { label: "Encounters", type: "Encounter", query: "Encounter" },
  { label: "Care Plans", type: "CarePlan", query: "CarePlan?category=assess-plan" },
  { label: "Care Teams", type: "CareTeam", query: "CareTeam?status=active" },
  { label: "Goals", type: "Goal", query: "Goal" },
  { label: "Service Requests", type: "ServiceRequest", query: "ServiceRequest" },
  // fhir-client's patient.request cannot scope Device (no compartment param), so it carries the patient param explicitly through plain client.request; Specimen is not pulled since Epic's Specimen API 400s on a patient-level search.
  { label: "Devices", type: "Device", query: "Device?patient=", explicitPatient: true },
  { label: "Questionnaire Responses", type: "QuestionnaireResponse", query: "QuestionnaireResponse" },
];

// Pull one resource type and write each item to JHE, isolated so one type's failure doesn't abort the others; seenIds (optional Set) dedupes ids an earlier pull of the same run already wrote, e.g. an Observation categorized as both laboratory and vital-signs.
async function eppPullResourceType(client, jheToken, sourceId, pull, iss, seenIds) {
  var resources;
  try {
    // A single instance read (Patient) uses plain client.request, since patient.request's injected ?patient= filter is rejected on an instance read; searches stay on patient.request so they're scoped to this patient.
    var result = pull.single
      ? await client.request(pull.query + "/" + client.patient.id)
      : pull.explicitPatient
        ? await client.request(pull.query + client.patient.id, { pageLimit: 0, flat: true })
        : await client.patient.request(pull.query, { pageLimit: 0, flat: true });
    resources = pull.single ? (result ? [result] : []) : result || [];
  } catch (e) {
    return { written: 0, failed: 0, error: e && e.message ? e.message : String(e), reasons: {}, warnings: {} };
  }
  var written = 0;
  var failed = 0;
  // Distinct failure/warning text -> count, so 45 identical messages read as one line; null prototype so a diagnostics string like "__proto__" counts as a plain key.
  var reasons = Object.create(null);
  var warnings = Object.create(null);
  var candidates = [];
  for (var i = 0; i < resources.length; i++) {
    var resource = resources[i];
    if (!resource || resource.resourceType !== pull.type) continue;
    if (seenIds && resource.id && seenIds.has(resource.id)) continue;
    // Over-64-char Epic ids ("Unconstrained FHIR IDs") are handled server-side -- the import moves them into an identifier and keys the upsert on them -- so the id must survive here.
    candidates.push(resource);
  }
  // Chunked Bundle posts, not one POST per record: the import endpoint replies per entry, so a few-hundred-lab pull is a handful of round trips.
  var BUNDLE_CHUNK = 100;
  for (var start = 0; start < candidates.length; start += BUNDLE_CHUNK) {
    var chunk = candidates.slice(start, start + BUNDLE_CHUNK);
    var writes = await eppWriteBundle(jheToken, sourceId, chunk);
    for (var j = 0; j < chunk.length; j++) {
      var write = writes[j];
      if (write.ok) {
        written++;
        // Mark seen only after a successful write, so a record that failed in one pull (e.g. Labs) is retried by a later pull that returns it (e.g. Vital Signs).
        if (seenIds && chunk[j].id) seenIds.add(chunk[j].id);
        (write.warnings || []).forEach(function (w) {
          warnings[w] = (warnings[w] || 0) + 1;
        });
      } else {
        failed++;
        var reason = write.reason || "unknown error";
        reasons[reason] = (reasons[reason] || 0) + 1;
      }
    }
  }
  return { written: written, failed: failed, error: null, reasons: reasons, warnings: warnings };
}

// Search hospital brands for the picker. Returns an array of facility rows (or []).
async function eppSearchBrands(jheToken, query) {
  var url = API_ENDPOINT + "ehr-patient-portal/brands?q=" + encodeURIComponent(query || "");
  var response = await fetch(url, {
    headers: { Authorization: "Bearer " + jheToken, "Cache-Control": "no-cache" },
  });
  if (!response.ok) return [];
  var data = await response.json();
  return data.results || [];
}

// Launch the Epic SMART authorize against the selected hospital's FHIR base URL (iss); fhir-client.js discovers the authorize/token endpoints from {iss}/.well-known/smart-configuration, so no per-hospital endpoint config is needed.
function eppAuthorizeWithIss(config, iss) {
  FHIR.oauth2.authorize({
    iss: iss,
    clientId: config.clientId,
    scope: config.scope,
    redirectUri: window.location.origin + "/clients/ehr-patient-portal/callback",
    pkceMode: "ifSupported",
  });
}

// Render hospital search results as clickable rows (name + address); clicking a row calls onSelect(row). Returns the number of rows rendered (0 => shows a message).
function eppRenderBrandResults(container, results, onSelect) {
  container.innerHTML = "";
  if (!results || results.length === 0) {
    var empty = document.createElement("div");
    empty.className = "text-muted p-2";
    empty.textContent = "No hospitals found. Try a different name, city, or state.";
    container.appendChild(empty);
    return 0;
  }
  var list = document.createElement("div");
  list.className = "list-group text-start";
  results.forEach(function (row) {
    var item = document.createElement("button");
    item.type = "button";
    item.className = "list-group-item list-group-item-action";
    item.setAttribute("data-brand-result", "");
    var title = document.createElement("div");
    title.className = "fw-bold";
    var facility = row.facilityName && row.facilityName !== row.brandName ? " — " + row.facilityName : "";
    title.textContent = row.brandName + facility;
    var addr = document.createElement("div");
    addr.className = "small text-muted";
    addr.textContent = row.addressText || "";
    item.appendChild(title);
    item.appendChild(addr);
    item.addEventListener("click", function () {
      onSelect(row);
    });
    list.appendChild(item);
  });
  container.appendChild(list);
  return results.length;
}

// Connect step: the hospital picker; picking a row launches the SMART authorize against that hospital.
pfClient.connect = async function (source) {
  eppStoreSourceId(source.id);
  pfRender("t-connect", { rail: pfRail(1) });
  var picker = {
    container: document.getElementById("hospital-picker"),
    input: document.getElementById("hospital-search"),
    results: document.getElementById("hospital-results"),
  };
  var jheToken = getStoredToken();
  var onSelect = function (row) {
    eppStoreBrandLocationId(row.id);
    eppAuthorizeWithIss(PATIENT_PORTAL_CONFIG, row.fhirBaseUrl);
  };
  var runSearch = async function () {
    eppRenderBrandResults(picker.results, await eppSearchBrands(jheToken, picker.input.value), onSelect);
  };
  var timer = null;
  picker.input.addEventListener("input", function () {
    if (timer) clearTimeout(timer);
    timer = setTimeout(runSearch, 200);
  });
  picker.container.hidden = false;
  await runSearch();
};

// The failure text of an import log: every type failed to fetch, or the last "Error:" line; null on success.
function eppImportFailure(log) {
  if (log.indexOf("could not fetch") !== -1 && log.indexOf("saved ") === -1) return "none of your record types could be fetched";
  var lines = log.split("\n");
  for (var i = lines.length - 1; i >= 0; i--) {
    if (lines[i].indexOf("Error:") === 0) return lines[i].slice("Error:".length).trim();
  }
  return null;
}

// Callback page entry: importing screen, run the import, then the done screen (or the error callout).
async function eppCallback() {
  pfRegisterPartials();
  await renderImporting();
  var out = document.getElementById("out");
  var config = PATIENT_PORTAL_CONFIG;
  try {
    await finishEhrPatientPortalConnect(out, config);
  } catch (e) {
    out.textContent += "\nError: " + (e && e.message ? e.message : e);
  }
  var failure = eppImportFailure(out.textContent);
  var sourceParam = "&source=" + (eppGetSourceId() || config.dataSourceIds[0]);
  if (failure) {
    showFlowError("We couldn't reach your healthcare organization", failure, {
      retryLabel: "Choose a different organization",
      retryHref: config.pageUrl + "?route=connect" + sourceParam,
    });
    return;
  }
  window.location.replace(config.pageUrl + "?route=done" + sourceParam);
}

// Callback page entry point: finish Epic handshake, store id, pull USCDI records, write to JHE.
async function finishEhrPatientPortalConnect(out, config) {
  out.textContent = "Completing connection...";
  var jheToken = getStoredToken();
  if (!jheToken) {
    out.textContent += "\nError: no JHE session. Restart from your invitation link.";
    return;
  }

  var client;
  try {
    client = await FHIR.oauth2.ready();
  } catch (e) {
    out.textContent += "\nError: EHR Patient Portal authorization failed: " + (e && e.message ? e.message : e);
    return;
  }

  // The token must carry patient context (the launch/patient scope), or we cannot attribute or scope the data -- stop with a clear message.
  var epicPatientId = client.patient && client.patient.id;
  if (!epicPatientId) {
    out.textContent += "\nError: no patient context from EHR Patient Portal (missing launch/patient scope)";
    return;
  }
  out.textContent += "\nEHR patient id: " + epicPatientId;

  // Provenance must be the hospital the patient actually picked and authorized against, which fhir-client records as state.serverUrl -- not any single configured default.
  var iss = client.state && client.state.serverUrl;
  if (!iss) {
    out.textContent += "\nError: no FHIR server URL from EHR Patient Portal authorization";
    return;
  }

  var idOk = await eppSavePatientIdentifier(jheToken, iss, epicPatientId);
  if (!idOk) {
    out.textContent += "\nError: failed to store EHR Patient Portal patient id";
    return;
  }
  out.textContent += "\nStored EHR Patient Portal patient id in JHE";

  var sourceId = await eppCreateFhirSource(jheToken, iss, eppGetSourceId() || config.dataSourceIds[0]);
  if (!sourceId) {
    out.textContent += "\nError: failed to register data source";
    return;
  }

  // Pull each USCDI type independently; pageLimit:0 + flat:true makes fhir-client.js follow every `next` link so patients with more than one page of records aren't truncated.
  var summary = [];
  var observationSeen = new Set(); // dedupe across the per-category Observation pulls
  for (var p = 0; p < EHR_PATIENT_PORTAL_PULLS.length; p++) {
    var pull = EHR_PATIENT_PORTAL_PULLS[p];
    out.textContent += "\n\nFetching " + pull.label + " from EHR Patient Portal...";
    var result;
    try {
      result = await eppPullResourceType(
        client, jheToken, sourceId, pull, iss,
        pull.type === "Observation" ? observationSeen : undefined
      );
    } catch (e) {
      // Belt over eppPullResourceType's own isolation: nothing may abort the loop and freeze the page mid-connect with the remaining types silently skipped.
      result = { written: 0, failed: 0, error: e && e.message ? e.message : String(e), reasons: {}, warnings: {} };
    }
    if (result.error) {
      out.textContent += "\n  could not fetch " + pull.label + ": " + result.error;
      summary.push(pull.label + ": fetch failed");
      continue;
    }
    out.textContent += "\n  saved " + result.written + " record(s)";
    var warningList = Object.keys(result.warnings || {});
    if (warningList.length) {
      // Saved-with-changes must be visible (RFC 0003), e.g. Conditions whose missing clinicalStatus was defaulted to 'unknown' -- same cap + console pattern as failures.
      console.warn("EHR Patient Portal import warnings for " + pull.label + ":", result.warnings);
      out.textContent += "\n  some saved record(s) were adjusted during import:";
      warningList
        .sort(function (a, b) {
          return result.warnings[b] - result.warnings[a];
        })
        .slice(0, 5)
        .forEach(function (warning) {
          out.textContent += "\n    - " + warning + " (x" + result.warnings[warning] + ")";
        });
      if (warningList.length > 5) {
        out.textContent += "\n    ... and " + (warningList.length - 5) + " more distinct warning(s)";
      }
    }
    if (result.failed) {
      out.textContent += "\n  " + result.failed + " record(s) could not be saved:";
      // The on-screen list below is capped; log the complete map so a console capture keeps every distinct reason.
      console.error("EHR Patient Portal import failures for " + pull.label + ":", result.reasons);
      // Validation messages can embed record values, making every reason distinct -- cap the list at the 5 most frequent so one bad type cannot flood the page.
      var reasonList = Object.keys(result.reasons).sort(function (a, b) {
        return result.reasons[b] - result.reasons[a];
      });
      reasonList.slice(0, 5).forEach(function (reason) {
        out.textContent += "\n    - " + reason + " (x" + result.reasons[reason] + ")";
      });
      if (reasonList.length > 5) {
        out.textContent += "\n    ... and " + (reasonList.length - 5) + " more distinct error(s)";
      }
    }
    summary.push(pull.label + ": " + result.written + (result.failed ? " (" + result.failed + " failed)" : ""));
  }

  out.textContent += "\n\nThe following information was added to JupyterHealth:\n\n" + summary.join("\n");
}

// Exposed for unit tests; browser runs load this as a plain <script> and ignore it.
if (typeof window !== "undefined") {
  window.eppPullResourceType = eppPullResourceType;
  window.eppWriteResource = eppWriteResource;
  window.eppWriteBundle = eppWriteBundle;
  window.EHR_PATIENT_PORTAL_PULLS = EHR_PATIENT_PORTAL_PULLS;
  window.eppSearchBrands = eppSearchBrands;
  window.eppAuthorizeWithIss = eppAuthorizeWithIss;
  window.eppRenderBrandResults = eppRenderBrandResults;
  window.eppSavePatientIdentifier = eppSavePatientIdentifier;
  window.eppStoreBrandLocationId = eppStoreBrandLocationId;
  window.eppStoreSourceId = eppStoreSourceId;
  window.eppGetSourceId = eppGetSourceId;
  window.finishEhrPatientPortalConnect = finishEhrPatientPortalConnect;
  window.eppImportFailure = eppImportFailure;
  window.eppCallback = eppCallback;
}
