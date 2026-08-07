# RFC 0003: Patient-access pull coverage + Observation views in the FHIR browser

- **Status:** Discussion
- **Companion PR:** #681 (stacked on #680) · **Builds on:** #671 (patient-access), #667 (FHIR search)
- **Follows:** the provenance-manifest format of RFC 0001 (#677) and RFC 0002 (#679)

This document explains two coupled changes shipped in #681: (1) expanding the
patient-access EHR sync from a 5-type demo subset to the full set of clinical
data JHE can ingest today, and (2) making imported Observations visible in the
JHE FHIR Resources browser, where they were previously unreachable by design.
It records why the coverage line sits where it does, the store-routing model
that made labs invisible, and the decisions taken in live testing on
2026-07-31.

---

## 1. Problem

### 1.1 The sync pulled a demo subset while everything downstream was ready for more

`PATIENT_ACCESS_PULLS` (the client-side pull list) covered five things:
Patient, Condition, MedicationRequest, AllergyIntolerance, and lab-category
Observations — annotated in code as "the demo phenotype." Meanwhile:

- The **Epic app registration** (appId 55446) already lists essentially the
  full USCDI read/search API surface (verified against the app's scope
  listing on fhir.epic.com).
- **JHE's aux store** (`fhir_config.json`, `aux_resources`) accepts 28
  resource types, all with `__interaction: ['*']`.
- The **R4→R5 cross-version package** ships StructureMaps for the whole
  clinical set (Coverage, CarePlan, DiagnosticReport, DocumentReference,
  Encounter, Immunization, Procedure, MedicationDispense, …).

So the constraint was only ever the client pull list and the OAuth scopes the
client requests — two strings in our own repo.

### 1.2 Imported lab Observations were invisible in the FHIR Resources browser

Live sandbox testing imported 301 labs ("Labs: 301 saved") that then did not
appear in the browser, while Conditions/Medications/Allergies from the same
sync displayed fine. Root cause (by design, from #671's search routing in
`core/views/fhir.py`): **a FHIR search hits exactly one store**, selected by
the `_source` parameter — absent `_source` routes a *mapped* type to the
Django-mapped rows. Six types are mapped (Device, Group, Observation,
Organization, Patient, Practitioner) and any of them can also hold imported
aux rows — so imported Patient demographics were equally invisible; users
noticed Observations because that is where the volume is. Aux-only types
have no mapped store, which is why the other clinical types displayed.
Nothing was lost; the imported rows were simply never queried. The fix
therefore covers every mapped type the sync writes (Observation, Patient,
Device), not Observation alone.

## 2. Decisions (made interactively, recorded per the RFC 0001 process)

1. **Coverage rule (human):** "only request data from Epic that we can handle
   today in our FHIR aux." Operationalized as: pull every patient-compartment
   clinical type that has **both** an R4→R5 StructureMap **and** an
   `aux_resources` entry — and nothing else.
2. **Display rule (human):** show **all** available Observations, but do
   **not** add a separate store/source selector. Instead the resource
   dropdown itself splits Observation into category views:
   `Observation - Device Data` (OMH mapped store), `Observation - Labs`,
   `Observation - Vital Signs` (aux store + category filter).
3. **Category handling (question → finding):** distinguishing labs vs vitals
   was suspected to be hard; it is not. The aux config already indexes a
   `category` token search on `category.coding`, and the token matcher
   (`core/fhir/search.py`) matches bare codes system-agnostically — exactly
   what Epic emits. Zero server changes were needed for the views.
4. **Status-less Conditions import as `unknown` (human, reversing an earlier
   deferral):** R4 `Condition.clinicalStatus` is optional; R5 requires it,
   which rejected 45 of 67 sandbox Conditions. Decision: do not drop them —
   the R4 import path defaults an absent `clinicalStatus` to the value set's
   own escape hatch `unknown` (R5-compliant, correctly excluded by
   `clinical-status=active` filters) and emits a per-record OperationOutcome
   warning so the enrichment is visible, not silent. Confined to the R4
   import endpoint; the native R5 API's validation is unchanged.
5. **Coverage is not pulled (review finding):** R5 added a mandatory
   `Coverage.kind` that the stock StructureMap cannot produce, so every
   Coverage import fails validation — pulling it would violate decision 1.
   Excluded until a JHE patch map supplies `kind`/`insurer` (follow-up).

## 3. What changed

### 3.1 Pull list (client-patient-access.js)

18 typed pulls + two Observation category pulls, in display order:
Demographics, Conditions, Medications, Medication Dispenses, Allergies,
Immunizations, Procedures, **Labs**, **Vital Signs**, Diagnostic Reports,
Documents, Encounters, Care Plans, Care Teams, Goals, Family History,
Service Requests, Specimens, Devices, Questionnaire Responses. Three
queries carry Epic-required filters (`CarePlan?category=assess-plan`,
`CareTeam?status=active`, `DocumentReference?category=clinical-note` — US
Core's mandatory search combinations, which Epic enforces). The two
Observation category pulls share a per-run dedupe set: an Epic Observation
categorized as both `laboratory` and `vital-signs` (e.g. POC glucose)
imports once, not twice.

Deliberately **not** pulled:

- **Coverage** — every record is guaranteed to fail R5 validation (`kind` is
  new-in-R5 and mandatory; the stock map has no rule for it and drops
  `payor`). Verified empirically; excluded until a patch map exists.
- **Reference/meta types** — Practitioner, PractitionerRole, Location,
  Organization, Medication, Provenance, Binary, Group, and RelatedPerson
  (in the Patient compartment but demographic/administrative, not clinical).
  They arrive by reference from resources that cite them. (Resolving those
  references into stored aux rows is a possible follow-up, tracked below.)
- **DocumentReference note content** — the resource imports; its Binary
  attachment content is not fetched (needs a content pipeline + storage
  decision; follow-up).
- **Further Observation categories** (social-history, SDOH, smoking-status
  etc.) — Epic exposes them and the aux store would take them, but each pull
  should come with a browser view that displays it; adding categories is a
  one-line pull + one dropdown entry each, once wanted.

Per-type failures remain isolated (one type's error never aborts the rest)
and, since #680, report their per-record reasons on the import page. The
first real run of the expanded list is expected to surface new R4→R5
conversion edge cases exactly the way allergies did — that is the shakeout
mechanism working, not a regression.

### 3.2 Scopes (seed.py, JheClient aux_data)

One `patient/<Type>.read` scope per pulled type. The Epic app registration
already covers all of them, so no Epic portal work is needed for sandbox.
**Deploy note:** seed writes `aux_data` only on creation — the deployed
JheClient row carries the old 5-scope string and needs a one-time update
(planned post-merge, via a management shell on the fly app).

### 3.3 Resource views (client-jhe-admin.js)

The dropdown splits every mapped type the sync writes into per-store views,
each a client-side mapping onto the existing single-store search API
(`RESOURCE_VIEWS`):

| View | Store | Query added |
|---|---|---|
| Observation - Device Data | mapped (OMH native) | — (today's behavior) |
| Observation - Labs | aux (imported) | `_source:below=<jhe-fhir-source-base>/` + `category=laboratory` |
| Observation - Vital Signs | aux (imported) | same + `category=vital-signs` |
| Patient - JHE | mapped (JHE accounts) | — |
| Patient - Imported EHR | aux (imported) | `_source:below=<jhe-fhir-source-base>/` |
| Device - Data Sources | mapped (DataSource rows) | — |
| Device - Imported EHR | aux (imported) | `_source:below=<jhe-fhir-source-base>/` |

The `_source:below` prefix is a single hoisted constant mirroring
`JHE_FHIR_SOURCE_BASE` (`core/models/fhir_aux_resource.py`; the server
tolerates the trailing slash via `rstrip`). A profile-restored plain type
name (the server remembers the URL path, not the view) maps to that type's
first view — so a user who was on "Observation - Labs" restores to Device
Data; accepted, noted in cons. No server changes; no new endpoint; no
separate selector UI.

### 3.4 Import enrichment (core/views/fhir_import.py)

`_enrich_r5` defaults R5-mandatory-but-R4-optional fields after conversion,
per decision 4: today only `Condition.clinicalStatus` → `unknown`, always
paired with a per-record OperationOutcome warning naming what was defaulted.
The enrichment lives in the R4 import endpoint only — the native R5 API
rejects a status-less Condition exactly as before.

## 4. Alternatives considered

| Alternative | Why not |
|---|---|
| **A separate source selector** (JHE-native / imported / per-source dropdown) | Rejected by product decision — two coupled dropdowns for one mental model ("what data am I looking at") where named views read better. The `_source` API remains available for power users and future per-source filtering. |
| **Server-side cross-store union for Observation** | #671 deliberately made search single-store ("there is no cross-store union") — a union breaks pagination/count semantics and mixes provenance models. Splitting views keeps the invariant. |
| **Pull everything Epic offers** (incl. Binary, ExplanationOfBenefit, Media…) | Violates the coverage rule: no aux config and/or no StructureMap → the import would 4xx on every record. The line is "what we can handle today," revisited as `fhir_config.json` grows. |
| **Deriving the pull list from the CapabilityStatement at runtime** | Attractive (single source of truth, ties into RFC 0001) but premature — the pull list also encodes Epic query constraints (e.g. Observation requires a category filter) that a capability statement doesn't express. Left as a future direction once #676's config-vs-code question settles. |

## 5. Pros / cons

**Pros**
- Sync coverage now matches ingest capability exactly — the stated rule is
  enforceable by inspection (map + aux entry ⇔ pulled).
- Imported Observations become visible, split along the axis users think in
  (device data vs labs vs vitals), with zero server surface added.
- Scope string and pull list live next to each other with the invariant
  documented (one scope per pulled type).

**Cons / accepted risks**
- ~~Re-running Connect duplicates imported records~~ — resolved in-PR
  (review): the aux create path upserts on (FhirSource, resourceType,
  source resource id), backed by a conditional unique constraint whose
  migration also collapses previously accumulated duplicates. Within a
  single run the two Observation pulls additionally dedupe by id.
- First expanded sync will likely surface new per-type conversion failures
  (visible, isolated, and reportable — by design). Empirical pre-check: 14
  of the 15 added types convert to valid R5; two are lossy-but-valid
  (`Encounter.reasonCode` has no stock map rule; `Device.patient` was
  removed in R5 — harmless, since aux visibility scopes via FhirSource).
- More scopes on the consent screen: Epic will show the patient a longer
  grant list. Acceptable for a PGD-sync product whose purpose is whole-record
  import; revisit if consent UX becomes a concern.
- Resource views are hardcoded client-side; a new category pull (or a new
  mapped type the sync writes) without its view would be invisible again.
  The pairing rule is documented at both sites, and the pull list itself is
  now test-enforced: a parametrized backend test converts+validates a
  minimal R4 instance of every pulled type (the test that would have caught
  Coverage), and a companion test asserts the seeded scopes match the pull
  list exactly.
- ~~Sticky view restore lands on a type's first view~~ — resolved in-PR
  (review): the server persists the view-defining search params alongside
  the path and the client resolves them back to the named view.
- `_source:below` value is a hoisted client constant (mirrors
  `JHE_FHIR_SOURCE_BASE`); if that base ever changes, both move together.
- The Epic-required query filters (CarePlan/CareTeam/DocumentReference) are
  taken from US Core's mandatory search combinations; Epic's per-version
  behavior varies, so the sandbox e2e (follow-up 2) remains the arbiter.

## 6. Follow-ups

1. ~~One-time deployed `JheClient.aux_data` scope update on fly~~ — now a
   data migration (0043), applied automatically on deploy.
2. Sandbox end-to-end run of the expanded pull; triage surfaced conversion
   failures (now labeled per-record).
3. Condition `clinicalStatus` policy (deferred from #680).
4. DocumentReference Binary content pipeline; reference-resolution for cited
   Practitioner/Location/Medication resources.
5. Additional Observation category views (social-history etc.) as pulls are
   added — always in pull+view pairs.
6. ~~Idempotent re-import~~ — done in-PR: aux rows upsert on (FhirSource,
   resourceType, source id); migration 0044 dedupes and adds the constraint.
7. JHE patch maps (`fhir-cross-version-patches/`, mechanism exists, unused):
   Coverage `kind`/`insurer` (unblocks pulling Coverage) and
   `Encounter.reasonCode → reason.value`.

## 7. Provenance

Developed with Claude (Claude Code) during live end-to-end testing of the
patient-access sync on 2026-07-31 (Epic sandbox + deployed jhe fly.io app).
Sequence: sandbox sync failures diagnosed by replaying Epic-shaped resources
through the branch's own conversion engine (→ #680); the "labs don't
display" report traced to the single-store search routing by reading
`core/views/fhir.py` (not logs — the deployed app does not access-log);
coverage and category questions answered by cross-referencing the Epic app's
registered API list (screenshot provided by the human) against
`fhir_config.json` and the cross-version package contents. All scoping
decisions in §2 were made by the human; the tool proposed the candidate type
list and the view mapping. Before publication the stacked PRs were reviewed
by three parallel agent reviewers (backend correctness with empirical
conversion runs of every pulled type; frontend correctness incl. Epic search
requirements; architecture/claims falsification) — their findings produced
the Coverage exclusion, the Epic query filters, the Observation dedupe, the
Patient/Device views, the dual-store correction in §1.2, and the invariant
tests. Full transcripts available.
