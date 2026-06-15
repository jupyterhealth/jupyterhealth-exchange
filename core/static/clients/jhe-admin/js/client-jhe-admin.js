// ────────────────────────────────────────────────────
// This is a Single Page Application built with vanilla
// JavaScript and Handlebars, styled using Bootstrap.
// No npm, bundlers, or build tools are required. The
// implementation prioritizes clarity and ease of
// extension, favoring straightforward, verbose code
// over complex abstractions (e.g., React-style
// destructuring patterns).
//
// New features can be added by following existing code
// patterns, making it easy to extend and maintain.
// Because no build step is required, functionality can
// be tested or invoked directly from the browser's
// developer console.
// ────────────────────────────────────────────────────


// ────────────────────────────────────────────────────
// Global Constants
// ────────────────────────────────────────────────────

const ROUTE_PREFIX = "/clients/jhe-admin/";
const DEFAULT_ROUTE = "organizations";
const API_PATH = "/api/v1";

const ROUTES = {
  "jhe-settings": {
    label: "System Settings",
    iconClass: "bi-gear",
    action: "renderJheSettings",
    superuserOnly: true,
  },
  practitioners: {
    label: "Practitioners",
    iconClass: "bi-person-badge",
    action: "renderPractitioners",
    superuserOnly: true,
  },
  organizations: {
    label: "Organizations",
    iconClass: "bi-diagram-3",
    action: "renderOrganizations",
  },
  patients: {
    label: "Patients",
    iconClass: "bi-person-vcard",
    action: "renderPatients",
  },
  studies: {
    label: "Studies",
    iconClass: "bi-journals",
    action: "renderStudies",
  },
  observations: {
    label: "OMH Observations",
    iconClass: "bi-database",
    action: "renderObservations",
  },
  fhir: {
    label: "FHIR Resources",
    iconClass: "bi-fire",
    action: "renderFhir",
  },
  clients: {
    label: "Clients",
    iconClass: "bi-box",
    action: "renderClients",
  },
  "data-sources": {
    label: "Data Sources",
    iconClass: "bi-boxes",
    action: "renderDataSources",
  },
  debug: {
    label: "Debug",
    iconClass: "bi-bug",
    action: "renderDebug",
    superuserOnly: true,
  },
};

// ────────────────────────────────────────────────────
// Global Vars
// ────────────────────────────────────────────────────

const actions = {
  renderJheSettings,
  renderPractitioners,
  renderOrganizations,
  renderPatients,
  renderStudies,
  renderObservations,
  renderFhir,
  renderClients,
  renderDataSources,
  renderDebug,
};
let crudModal;
let store = {};
let userProfile = {};
let signingOut = false;
let showDelayedElementsTimeoutId = null;
let navLoadingOverlayCounter = 0;

// ────────────────────────────────────────────────────
// Common
// ────────────────────────────────────────────────────

async function app() {
  let currentRouteAndParams = getCurrentRouteAndParams();
  if (!ROUTES[currentRouteAndParams.route])
    currentRouteAndParams.route = DEFAULT_ROUTE;
  await nav(currentRouteAndParams.route, currentRouteAndParams.params);
}

function showNavLoadingOverlay() {
  const overlay = document.getElementById("navLoadingOverlay");
  if (overlay) {
    const shouldStartTimer = navLoadingOverlayCounter === 0;
    navLoadingOverlayCounter++;

    overlay.style.display = "flex";

    const closeButton = document.getElementById("cancelLoadingBtn");
    if (closeButton) closeButton.style.display = "none";

    if (shouldStartTimer && showDelayedElementsTimeoutId) {
      clearTimeout(showDelayedElementsTimeoutId);
      showDelayedElementsTimeoutId = null;
    }

    if (shouldStartTimer) {
      showDelayedElementsTimeoutId = setTimeout(() => {
        if (navLoadingOverlayCounter > 0 && closeButton) {
          closeButton.style.display = "block";
          setTimeout(() => closeButton.focus(), 50);
        }
        showDelayedElementsTimeoutId = null;
      }, 10000);
    }
  }
}

function hideNavLoadingOverlay() {
  const overlay = document.getElementById("navLoadingOverlay");
  if (overlay) {
    navLoadingOverlayCounter = Math.max(0, navLoadingOverlayCounter - 1);

    if (navLoadingOverlayCounter <= 0) {
      navLoadingOverlayCounter = 0;
      overlay.style.display = "none";

      if (showDelayedElementsTimeoutId) {
        clearTimeout(showDelayedElementsTimeoutId);
        showDelayedElementsTimeoutId = null;
      }

      const closeButton = document.getElementById("cancelLoadingBtn");
      if (closeButton) closeButton.style.display = "none";
    }
  }
}

async function nav(newRoute, queryParams, appendQueryParams) {
  showNavLoadingOverlay();
  try {
    // Block non-superusers from navigating to superuser-only routes
    if (ROUTES[newRoute]?.superuserOnly) {
      if (!userProfile || !userProfile.email) {
        userProfile = await getUserProfile();
      }
      if (!userProfile.isSuperuser) {
        newRoute = DEFAULT_ROUTE;
      }
    }

    const newRouteSettings = ROUTES[newRoute];
    const current = getCurrentRouteAndParams();

    if (!queryParams) {
      queryParams = appendQueryParams
        ? { ...current.params, ...appendQueryParams }
        : {};
    }

    console.log(`nav() - queryParams: ${JSON.stringify(queryParams)}`);

    const bodyTemplate = Handlebars.compile(
      document.getElementById("t-body").innerHTML
    );

    // Ensure user is authenticated
    if (!(await userManager.getUser())) {
      // If a prior sign-in failed (eg. an OAuth error from the server), show it
      // instead of silently re-redirecting, which would loop indefinitely (#192).
      const authError = getAuthError();
      if (authError) {
        displayError(authError);
        return;
      }
      console.log("nav() - no user found, calling signinRedirect()");
      await userManager.signinRedirect();
      return;
    }

    // Render main content for the route
    const mainContent = await actions[newRouteSettings.action](queryParams);

    // Remove any existing main content
    const oldMain = document.getElementById("mainContent");
    if (oldMain) oldMain.remove();

    // Build nav items (hide superuser-only routes for non-superusers)
    if (!userProfile || !userProfile.email) {
      userProfile = await getUserProfile();
    }
    const navItems = Object.entries(ROUTES)
      .filter(([, settings]) => !settings.superuserOnly || userProfile.isSuperuser)
      .map(([route, settings]) => ({
        ...settings,
        active: route === newRoute,
        route,
      }));

    // Replace body content
    const baseBody = document.getElementById("baseBody");
    document
      .querySelectorAll("#baseBody > main")
      .forEach((child) => baseBody.removeChild(child));

    document
      .getElementById("baseBody")
      .insertAdjacentHTML(
        "afterbegin",
        bodyTemplate({ navItems, mainContent })
      );

    // Post-render hooks
    renderUserProfile();
    document.getElementById("jheVersion").textContent = CONSTANTS.JHE_VERSION;

    const crudModalElement = document.getElementById(`${newRoute}-crudModal`);
    if (crudModalElement) {
      crudModal = new bootstrap.Modal(crudModalElement, {});

      // avoid duplicate listeners on re-render
      if (!crudModalElement.dataset.shownHookBound) {
        crudModalElement.dataset.shownHookBound = "1";
        crudModalElement.addEventListener("shown.bs.modal", () => {
          window.MODAL_SHOWN_HANDLERS?.[newRoute]?.(crudModalElement);
        });
      }
    }

    if (
      queryParams.create ||
      queryParams.read ||
      queryParams.update ||
      queryParams.delete
    ) {
      crudModal.show();
    }

    // Push history if route/params changed
    if (
      newRoute !== current.route ||
      !isShallowEq(queryParams, current.params)
    ) {
      // Strip null values (e.g. studyId: null means "All Studies" — present as a key
      // to suppress settings restoration, but must not appear in the URL as "studyId=null")
      const urlParams = Object.fromEntries(
        Object.entries(queryParams).filter(([, v]) => v != null)
      );
      window.history.pushState(
        {},
        "",
        ROUTE_PREFIX +
          newRoute +
          "?" +
          new URLSearchParams(urlParams).toString()
      );
    }
  } catch (e) {
    console.error(e);
    displayError(e?.message || e);
  } finally {
    hideNavLoadingOverlay();
  }
}

function navReload() {
  clearModalValidationErrors();
  if (crudModal._isShown) crudModal.hide();
  const currentRouteAndParams = getCurrentRouteAndParams();
  return nav(currentRouteAndParams.route, currentRouteAndParams.params);
}

function navReloadModal() {
  const currentRouteAndParams = getCurrentRouteAndParams();
  // just re-fetch current route content without hiding modal
  crudModal.hide();
  crudModal.show();
  const params = currentRouteAndParams.params;
  nav(currentRouteAndParams.route, params);
}

window.addEventListener("popstate", function (event) {
  console.log("popstate EventListener", JSON.stringify(event));
  if (!signingOut) navReload(); // see signOut() for explanation
});

async function navReturnFromCrud() {
  const currentRouteAndParams = getCurrentRouteAndParams();
  const params = Object.fromEntries(
    Object.entries(currentRouteAndParams.params).filter(([k]) =>
      ["organizationId", "tloId"].includes(k)
    )
  );
  crudModal.hide(); // This returns immediately but kicks of an async process
  await new Promise((resolve) => setTimeout(resolve, 600)); // wait for modal to hide (300 ms)
  // Must await: a caller that navigates again right after (e.g. patient lookup -> create,
  // which passes lookedUpEmail) would otherwise race this detached nav and the later one
  // could be clobbered, dropping the looked-up email from the create form.
  await nav(currentRouteAndParams.route, params);
}

function getCurrentRouteAndParams() {
  let currentRoute = window.location.pathname.substring(ROUTE_PREFIX.length);
  if (currentRoute.endsWith("/")) currentRoute = currentRoute.slice(0, -1);
  const params = Object.fromEntries(
    new URLSearchParams(document.location.search)
  );
  return {
    route: currentRoute,
    params: params,
  };
}

async function apiRequest(method, resourcePath, query) {
  console.log(
    `apiRequest: ${method} ${resourcePath} ${JSON.stringify(query)}`
  );
  const headers = {
    "Cache-Control": "no-cache",
  };
  const user = await userManager.getUser();
  if (user) headers["Authorization"] = `Bearer ${user.access_token}`;
  // FHIR resources are served at the site root (e.g. /FHIR/R5/Patient), not under API_PATH.
  let url = resourcePath.startsWith("FHIR")
    ? "/" + resourcePath
    : API_PATH + "/" + resourcePath;
  let body;
  if (query) {
    if (method === "GET") {
      url = `${url}?${new URLSearchParams(query).toString()}`;
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(query);
    }
  }
  let response = null;
  try {
    response = await fetch(url, {
      method: method,
      headers: headers,
      body: body,
    });
    console.log(
      `apiRequest response: ${response.status} ${response.statusText}`
    );
    // Unauthorized
    if (parseInt(response.status) == 401) {
      // Don't re-redirect into a failed OAuth flow (#192) - surface the error.
      const authError = getAuthError();
      if (authError) {
        displayError(authError);
        return;
      }
      await userManager.signinRedirect();
      return;
    } else if (parseInt(response.status) == 400) {
      displayModalValidationError(await response.json());
      return;
    } else if (parseInt(response.status) > 299) {
      const message = await extractApiErrorMessage(response);
      // Expected 4xx conditions raised from an open modal (e.g. "Practitioner
      // not found" when adding a user) belong in that modal's banner, not the
      // alarming page-level "click Refresh" alert. Server faults still use it.
      if (parseInt(response.status) < 500 && document.querySelector(".modal.show")) {
        displayModalValidationError([message]);
      } else {
        displayError(message);
      }
      return;
    }
  } catch (error) {
    displayError(error);
    console.log(`apiRequest Error: ${error}`);
  }
  return response;
}

function displayError(messageDetail) {
  const MESSAGE =
    "An Error has occured. Please click your browser Refresh button and try again.";
  const e = document.getElementById("errorAlert");
  e.innerHTML = `${MESSAGE}<br/><small>Detail: ${messageDetail}</small>`;
  e.style.display = "block";
}

function displayModalValidationError(messages) {
  let htmlMesssage = "";
  if (Array.isArray(messages)) {
    htmlMesssage = messages.join("; ");
  } else {
    Object.keys(messages).forEach((field) => {
      htmlMesssage += `<li>${field} - ${messages[field][0]}</li>`;
    });
  }
  document.querySelectorAll(".validationError").forEach((e) => {
    e.innerHTML = `<small>Validation Error(s): ${htmlMesssage}</small>`;
    e.style.display = "block";
  });
}

function clearModalValidationErrors() {
  document.querySelectorAll(".validationError").forEach((e) => {
    e.style.display = "none";
  });
}

// Page-level (non-modal) notice. Render guards return this markup as content
// (the body shell re-renders after they run, so a live element would be wiped);
// list actions that fire after render call displayPageNotice() on #pageNotice.
function pageNoticeMarkup(message) {
  return `<div class="alert alert-warning" role="alert"><small>${message}</small></div>`;
}

function displayPageNotice(message) {
  const e = document.getElementById("pageNotice");
  if (!e) return;
  e.innerHTML = pageNoticeMarkup(message);
  e.style.display = "block";
}

function clearPageNotice() {
  const e = document.getElementById("pageNotice");
  if (e) e.style.display = "none";
}

async function extractApiErrorMessage(response) {
  try {
    const body = await response.json();
    if (typeof body === "string") return body;
    return body.error || body.detail || response.statusText;
  } catch {
    return response.statusText;
  }
}

async function hasOrganizationPermission(
  selectedOrganization,
  organizationId,
  permission
) {
  let role = selectedOrganization?.currentUserRole;
  if (!role && organizationId) {
    try {
      const organizationResponse = await apiRequest(
        "GET",
        `organizations/${organizationId}`
      );
      const organization = await organizationResponse.json();
      role = organization?.currentUserRole;
    } catch {
      return false;
    }
  }
  return role ? ifRoleCan(role, permission) : false;
}

async function hasGlobalPermission(permission) {
  if ((await getUserProfile()).isSuperuser) {
    return ifRoleCan("super_user", permission);
  }
  return false;
}

// ────────────────────────────────────────────────────
// User Profile
// ────────────────────────────────────────────────────

async function getUserProfile() {
  const user = await userManager.getUser();
  const userProfileResponse = await apiRequest("GET", `users/profile`);
  const localUserProfile = await userProfileResponse.json();
  if (parseInt(user.profile.sub) !== parseInt(localUserProfile.id)) signOut();
  return localUserProfile;
}

async function renderUserProfile() {
  if (!userProfile || !userProfile.email) {
    userProfile = await getUserProfile();
  }
  let displayName = userProfile.email.substring(0, 12) + "...";
  // if (userProfile.firstName && userProfile.lastName) {
  //   displayName = `${userProfile.firstName} ${userProfile.lastName}`
  // }
  document.getElementById("profileUsername").textContent = displayName;
}

// userManager.removeUser() raises an event which triggers
// the popstate listner which calls navReload to catch back
// button events. Skip this for signout otherwise redirect
// is never processed.
async function signOut() {
  signingOut = true;
  await userManager.removeUser();
  this.document.location = "/accounts/logout";
}

// ────────────────────────────────────────────────────
// Permission helper (must appear before any render*())
// ────────────────────────────────────────────────────
function ifRoleCan(role, permission) {
  return (CONSTANTS.ROLE_PERMISSIONS[role] || []).includes(permission);
}

// ────────────────────────────────────────────────────
// Organizations
// ────────────────────────────────────────────────────

async function renderOrganizations(queryParams) {
  const content = Handlebars.compile(
    document.getElementById("t-organizations").innerHTML
  );
  const topLevelOrganizationsResponse = await apiRequest(
    "GET",
    "organizations",
    {
      partOf: CONSTANTS.ORGANIZATION_TOP_LEVEL_PART_OF_ID,
    }
  );
  const topLevelOrganizationsPaginated =
    await topLevelOrganizationsResponse.json();
  let topLevelOrganizationsSelect = topLevelOrganizationsPaginated.results;
  let organizationTreeChildren = [];
  let canManagePractitionersInOrg;
  let organizationRecord = null;
  // If a top level organization is selected
  if (queryParams.tloId && queryParams.tloId != 0) {
    topLevelOrganizationsSelect = topLevelOrganizationsSelect.map(
      (organization) => {
        organization.selected = organization.id === parseInt(queryParams.tloId);
        return organization;
      }
    );
    const organizationRecordResponse = await apiRequest(
      "GET",
      `organizations/${queryParams.tloId}`
    );
    organizationRecord = await organizationRecordResponse.json();
    organizationRecord.typeSelect = buildSelectOptions(
      CONSTANTS.ORGANIZATION_TYPES,
      organizationRecord.type,
      ["root"]
    );
    if (organizationRecord && organizationRecord.currentUserRole) {
      canManagePractitionersInOrg = ifRoleCan(
        organizationRecord.currentUserRole,
        "organization.manage_for_practitioners"
      );
    }

    const organizationTreeResaponse = await apiRequest(
      "GET",
      `organizations/${queryParams.tloId}/tree`
    );
    const organizationTree = await organizationTreeResaponse.json();
    // Each node's manage permission is based on the user's role in that node
    // itself, not the top-level organization (a manager of a sub-org may have
    // no role in the root). Annotate every node so the tree can show the right
    // action icons per node.
    const annotateCanManage = (organizations) => {
      (organizations || []).forEach((organization) => {
        organization.canManage = ifRoleCan(
          organization.currentUserRole,
          "organization.manage_for_practitioners"
        );
        annotateCanManage(organization.children);
      });
    };
    annotateCanManage(organizationTree.children);
    organizationTreeChildren = organizationTree.children;
  }

  let partOfId, partOfName;

  if (queryParams.create) {
    if (
      queryParams.partOf &&
      queryParams.partOf == CONSTANTS.ORGANIZATION_TOP_LEVEL_PART_OF_ID
    ) {
      partOfId = CONSTANTS.ORGANIZATION_TOP_LEVEL_PART_OF_ID;
      partOfName = CONSTANTS.ORGANIZATION_TOP_LEVEL_PART_OF_LABEL;
    } else {
      if (queryParams.tloId) {
        partOfId = queryParams.partOf ? queryParams.partOf : queryParams.id;
        const organizationRecordPartOfResponse = await apiRequest(
          "GET",
          `organizations/${partOfId}`
        );
        const organizationRecordPartOf =
          await organizationRecordPartOfResponse.json();
        partOfId = organizationRecordPartOf.id;
        partOfName = organizationRecordPartOf.name;
      } else {
        partOfId = CONSTANTS.ORGANIZATION_TOP_LEVEL_PART_OF_ID;
        partOfName = CONSTANTS.ORGANIZATION_TOP_LEVEL_PART_OF_LABEL;
      }
    }
    organizationRecord = {
      partOfId: partOfId,
      partOfName: partOfName,
      typeSelect: buildSelectOptions(CONSTANTS.ORGANIZATION_TYPES, null, [
        "root",
      ]),
    };
  } else if (queryParams.update || queryParams.read || queryParams.delete) {
    const organizationRecordResponse = await apiRequest(
      "GET",
      `organizations/${queryParams.id}`
    );
    organizationRecord = await organizationRecordResponse.json();
    organizationRecord.typeSelect = buildSelectOptions(
      CONSTANTS.ORGANIZATION_TYPES,
      organizationRecord.type,
      ["root"]
    );
    if (organizationRecord && organizationRecord.currentUserRole) {
      canManagePractitionersInOrg = ifRoleCan(
        organizationRecord.currentUserRole,
        "organization.manage_for_practitioners"
      );
    }
    if (
      organizationRecord.partOf == CONSTANTS.ORGANIZATION_TOP_LEVEL_PART_OF_ID
    ) {
      organizationRecord.partOfName =
        CONSTANTS.ORGANIZATION_TOP_LEVEL_PART_OF_LABEL;
    } else {
      const organizationRecordParentResponse = await apiRequest(
        "GET",
        `organizations/${organizationRecord.partOf}`
      );
      const organizationRecordParent =
        await organizationRecordParentResponse.json();
      organizationRecord.partOfName = organizationRecordParent.name;
    }

    if (queryParams.read) {
      const organizationUsersResponse = await apiRequest(
        "GET",
        `organizations/${queryParams.id}/users`
      );
      organizationRecord.users = await organizationUsersResponse.json();
      const organizationStudiesResponse = await apiRequest(
        "GET",
        `organizations/${queryParams.id}/studies`
      );
      organizationRecord.studies = await organizationStudiesResponse.json();
    }
  }

  Handlebars.registerPartial(
    "recursiveOrganizationTree",
    document.getElementById("t-recursiveOrganizationTree").innerHTML
  );

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML
  );

  const renderParams = {
    ...queryParams,
    topLevelOrganizationsSelect: topLevelOrganizationsSelect,
    children: organizationTreeChildren,
    organizationRecord: organizationRecord,
    canManagePractitionersInOrg: canManagePractitionersInOrg,
    canCreateTopLevelOrganization: await hasGlobalPermission(
      "organization.create_top_level"
    ),
  };

  return content(renderParams);
}

function validateOrganizationForm() {
  const errors = [];
  const name =
    document.getElementById("organizationName")?.value?.trim() || "";
  const type = document.getElementById("organizationType")?.value || "";
  if (!name) errors.push("Name is required.");
  if (!type) errors.push("Type is required.");
  return errors;
}

async function createOrganization(partOf) {
  clearModalValidationErrors();
  const errors = validateOrganizationForm();
  if (errors.length) return displayModalValidationError(errors);
  const organizationName =
    document.getElementById("organizationName").value || null;
  const organizationType = document.getElementById("organizationType").value;
  const organizationRecord = {
    name: organizationName,
    type: organizationType,
    partOf: partOf,
  };
  const response = await apiRequest(
    "POST",
    "organizations",
    organizationRecord
  );
  if (response.ok) await navReturnFromCrud();
}

async function updateOrganization(id) {
  clearModalValidationErrors();
  const errors = validateOrganizationForm();
  if (errors.length) return displayModalValidationError(errors);
  const organizationName =
    document.getElementById("organizationName").value || null;
  const organizationType = document.getElementById("organizationType").value;
  const organizationRecord = {
    name: organizationName,
    type: organizationType,
  };
  const response = await apiRequest(
    "PATCH",
    `organizations/${id}`,
    organizationRecord
  );
  if (response.ok) await navReturnFromCrud();
}

async function deleteOrganization(id) {
  const response = await apiRequest("DELETE", `organizations/${id}`);
  if (response.ok) await navReturnFromCrud();
}

async function addUserToOrganization(userEmail, organizationId, role) {
  clearModalValidationErrors();
  const email = (userEmail || "").trim();
  if (!email) {
    return displayModalValidationError(["User e-mail is required."]);
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return displayModalValidationError([
      "User e-mail is not a valid e-mail address.",
    ]);
  }
  if (!organizationId) return;
  const userRecordResponse = await apiRequest("GET", "users/search_by_email", {
    email: email,
  });
  const userRecordPaginated = await userRecordResponse.json();
  if (userRecordPaginated.id === undefined) {
    return displayModalValidationError(["No User with this e-mail exists."]);
  }
  if (userRecordPaginated.patient) {
    return displayModalValidationError([
      `${email} is a patient account. Only practitioner accounts can be added to an organization. The Manager/Member/Viewer choice sets that practitioner's role.`,
    ]);
  }
  const response = await apiRequest(
    "POST",
    `organizations/${organizationId}/user`,
    {
      jheUserId: userRecordPaginated.id,
      organizationPartitionerRole: role,
    }
  );
  if (response.ok) navReloadModal();
}

async function removeUserFromOrganization(userId, organizationId) {
  if (!userId || !organizationId) return;
  const response = await apiRequest(
    "DELETE",
    `organizations/${organizationId}/remove_user`,
    {
      jheUserId: userId,
    }
  );
  if (response.ok) navReloadModal();
}

// ────────────────────────────────────────────────────
// Practitioners
// ────────────────────────────────────────────────────

async function renderPractitioners(queryParams) {

  const content = Handlebars.compile(
    document.getElementById("t-practitioners").innerHTML
  );

  let practitionersPaginated, practitionerRecord;

  const pageSize = parseInt(queryParams.pageSize) || 20;
  const page = parseInt(queryParams.page) || 1;

  const practitionersParams = {
    page: page,
    pageSize: pageSize,
  };

  const practitionersResponse = await apiRequest("GET", "practitioners", practitionersParams);
  practitionersPaginated = await practitionersResponse.json();

  // SJ: Why is this necessary? It's used in patients as well
  if (
    practitionersPaginated.results &&
    practitionersPaginated.results.length > pageSize
  ) {
    practitionersPaginated.results = practitionersPaginated.results.slice(0, pageSize);
  }

  // if the batch url has been reloaded and there are no longer any selected practitioners
  if(queryParams.batch && !store.practitionerIdsForBatchAction){
    nav("practitioners", {});
    return;
  }

  if (queryParams.read || queryParams.update || ( queryParams.delete && !queryParams.batch)) {
    const practitionerRecordResponse = await apiRequest(
      "GET",
      `practitioners/${queryParams.id}`
    );
    practitionerRecord = await practitionerRecordResponse.json();
  }

  Handlebars.registerHelper("eq", function (v1, v2) {
    return v1 === v2;
  });

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML
  );

  const renderParams = {
    ...queryParams,
    practitioners: practitionersPaginated?.results,
    practitionerRecord: practitionerRecord,
    page: page,
    pageSize: pageSize,
    totalPages: Math.ceil(practitionersPaginated.count / pageSize),
    pageSizes: [20, 100, 500, 1000],
    canManagePractitioners: true,
    selectedPractitionerIds: store.practitionerIdsForBatchAction,
    selectedCount: store.practitionerIdsForBatchAction?.length,
  };

  return content(renderParams);
}

async function selectPractitionerIdsForBatchAction() {
  const selectedRecordIds = getSelectedRecordIds(".practitioner-checkbox");
  if (selectedRecordIds.length == 0) {
    displayPageNotice("Please select one or more Practitioners.");
    return;
  }
  clearPageNotice();
  delete store.practitionerIdsForBatchAction;
  store.practitionerIdsForBatchAction = selectedRecordIds;
  nav("practitioners", { delete: true, batch: true });
}

async function updatePractitioner(id) {
  clearModalValidationErrors();
  const errors = validatePractitionerForm();
  if (errors.length) return displayModalValidationError(errors);
  const practitionerRecord = {
    nameFamily: document.getElementById("practitionerFamilyName").value || null,
    nameGiven: document.getElementById("practitionerGivenName").value || null,
  };
  let response = await apiRequest(
    "PATCH",
    `practitioners/${id}?organizationId=${
      document.getElementById("organizationForPractitioners")?.value
    }`,
    practitionerRecord
  );
  if (response.ok && document.getElementById("addOrganizationId")) {
    response = await apiRequest(
      "PATCH",
      `practitioners/${id}/global_add_organization?organizationId=${
        document.getElementById("addOrganizationId").value
      }`
    );
  }
  if (response.ok) await navReturnFromCrud();
}

async function deletePractitioner(id, batch) {
  let deleteList = []
  if (batch && store.practitionerIdsForBatchAction) {
    deleteList = store.practitionerIdsForBatchAction;
  } else {
    deleteList = [id];
  }
  for(const id of deleteList){
    await apiRequest(
      "DELETE",
      `practitioners/${id}`
    )
  }
  await navReturnFromCrud();
}

function validatePractitionerForm() {
  const errors = [];
  const family =
    document.getElementById("practitionerFamilyName")?.value?.trim() || "";
  const given =
    document.getElementById("practitionerGivenName")?.value?.trim() || "";
  if (!family) errors.push("Family name is required.");
  if (!given) errors.push("Given name is required.");
  return errors;
}


// ────────────────────────────────────────────────────
// Patients
// ────────────────────────────────────────────────────

async function renderPatients(queryParams) {
  const organizationsResponse = await apiRequest("GET", "users/organizations");
  const organizations = await organizationsResponse.json();

  if (organizations.length == 0) {
    return pageNoticeMarkup("This user does not belong to any Organization.");
  }

  // If no org is selected, lets check what they were last using in the profile,
  // otherwise default to the first organization in the list
  if (!queryParams.organizationId) {
    const currentOrganizationId = (await getUserProfile()).settings
      ?.currentOrganizationId;
    if (currentOrganizationId) {
      console.log(
        "Setting currentOrganizationId from user profile settings: ",
        currentOrganizationId,
      );
      queryParams.organizationId = currentOrganizationId;
    } else {
      queryParams.organizationId = organizations[0].id;
    }
  }
  let selectedOrganization;
  const organizationForPatientsSelect = organizations.map((organization) => {
    if (organization.id === parseInt(queryParams.organizationId)) {
      organization.selected = true;
      selectedOrganization = organization;
    } else {
      organization.selected = false;
    }
    return organization;
  });

  const studiesResponse = await apiRequest("GET", "studies", {
    organizationId: queryParams.organizationId,
  });
  const studies = await studiesResponse.json();

  // Restore the last-used study from settings when arriving without an explicit studyId
  // (e.g. navigating here from another route). If studyId is present as a key — even as
  // null, which "All Studies" passes — skip restoration so the user's choice is honoured.
  const currentStudyId = (await getUserProfile()).settings?.currentStudyId;
  if (currentStudyId && !("studyId" in queryParams)) {
    queryParams.studyId = currentStudyId;
  }

  const selectedStudyId = queryParams.studyId ? parseInt(queryParams.studyId) : null;
  const studyForPatientsSelect = studies.results.map((study) => {
    const studyIsSelected = selectedStudyId !== null && study.id === selectedStudyId;
    study.selected = studyIsSelected;
    return study;
  });

  const content = Handlebars.compile(
    document.getElementById("t-patients").innerHTML,
  );

  let patientsPaginated,
    patientRecord,
    studiesPendingConsent,
    studiesConsented,
    consolidatedClients;

  const pageSize = parseInt(queryParams.pageSize) || 20;
  const page = parseInt(queryParams.page) || 1;

  const patientsParams = {
    organizationId: queryParams.organizationId,
    page: page,
    pageSize: pageSize,
  };

  if (queryParams.studyId) {
    patientsParams["studyId"] = queryParams.studyId;
  }

  const patientsResponse = await apiRequest("GET", "patients", patientsParams);
  patientsPaginated = await patientsResponse.json();

  if (
    patientsPaginated.results &&
    patientsPaginated.results.length > pageSize
  ) {
    patientsPaginated.results = patientsPaginated.results.slice(0, pageSize);
  }

  if (queryParams.read || queryParams.update || queryParams.delete) {
    const patientRecordResponse = await apiRequest(
      "GET",
      `patients/${queryParams.id}`,
    );
    patientRecord = await patientRecordResponse.json();

    if (queryParams.read) {
      const patientRecordConsentsResponse = await apiRequest(
        "GET",
        `patients/${queryParams.id}/consents`,
      );
      patientRecordConsents = await patientRecordConsentsResponse.json();
      studiesPendingConsent = patientRecordConsents.studiesPendingConsent;
      studiesConsented = patientRecordConsents.studies;

      const patientRecordConsolidatedClientsResponse = await apiRequest(
        "GET",
        `patients/${queryParams.id}/consolidated_clients`,
      );
      consolidatedClients = (await patientRecordConsolidatedClientsResponse.json()).map((client) => ({
        ...client,
        patientInvitations: client.patientInvitations.map((inv) => ({
          ...inv,
          lastUpdated: inv.lastUpdated.slice(0, 10),
          status: inv.status.charAt(0).toUpperCase() + inv.status.slice(1),
        })),
      }));
    }
  } else if (queryParams.create && queryParams.lookedUpEmail) {
    patientRecord = {
      telecomEmail: queryParams.lookedUpEmail,
    };
  }

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML,
  );

  Handlebars.registerHelper("eq", function (v1, v2) {
    return v1 === v2;
  });

  const canManagePatientsInOrg = await hasOrganizationPermission(
    selectedOrganization,
    queryParams.organizationId,
    "patient.manage_for_organization",
  );

  const renderParams = {
    ...queryParams,
    patients: patientsPaginated?.results,
    patientRecord: patientRecord,
    hidePatientDetails: queryParams.create && !queryParams.lookedUpEmail,
    page: page,
    pageSize: pageSize,
    totalPages: Math.ceil(patientsPaginated.count / pageSize),
    organizationForPatientsSelect: organizationForPatientsSelect,
    selectedOrganization: selectedOrganization,
    studyForPatientsSelect: studyForPatientsSelect,
    studiesPendingConsent: studiesPendingConsent,
    studiesConsented: studiesConsented,
    pageSizes: [20, 100, 500, 1000],
    canManagePatientsInOrg: canManagePatientsInOrg,
    consolidatedClients: consolidatedClients,
  };

  return content(renderParams);
}

async function globalLookupPatientByEmail(email, organizationId) {
  clearModalValidationErrors();
  const lookupEmail = (email || "").trim();
  if (!lookupEmail) {
    return displayModalValidationError(["Patient e-mail is required."]);
  }
  if (!isValidPatientEmail(lookupEmail)) {
    return displayModalValidationError([
      "Patient e-mail is not a valid e-mail address.",
    ]);
  }
  const patientRecordResponse = await apiRequest(
    "GET",
    `patients/global_lookup`,
    { email: lookupEmail }
  );
  const patientRecord = await patientRecordResponse.json();
  if (
    patientRecord &&
    patientRecord[0] &&
    patientRecord[0].organizations &&
    patientRecord[0].organizations.length > 0
  ) {
    const matchingOrganization = patientRecord[0].organizations.find(
      (org) => org.id === organizationId
    );
    if (matchingOrganization) {
      return displayModalValidationError([
        `Patient with E-mail ${lookupEmail} is already a member of ${matchingOrganization.name}`,
      ]);
    }
    await navReturnFromCrud();
    await nav("patients", {
      update: true,
      id: patientRecord[0].id,
      organizationId: organizationId,
      addOrganizationId: true,
    });
  } else {
    await navReturnFromCrud();
    await nav("patients", {
      create: true,
      organizationId: organizationId,
      lookedUpEmail: lookupEmail,
    });
  }
}

async function createPatient(organizationId) {
  clearModalValidationErrors();
  const errors = validatePatientForm({ checkEmail: true });
  if (errors.length) return displayModalValidationError(errors);
  const patientRecord = {
    organizationId: organizationId,
    identifiers: collectPatientIdentifiers(),
    nameFamily: document.getElementById("patientFamilyName").value || null,
    nameGiven: document.getElementById("patientGivenName").value || null,
    birthDate: document.getElementById("patientBirthDate").value || null,
    telecomEmail: document.getElementById("patientTelecomEmail").value || null,
    telecomPhone: document.getElementById("patientTelecomPhone").value || null,
  };
  const response = await apiRequest("POST", `patients`, patientRecord);
  if (response.ok) await navReturnFromCrud();
}

async function updatePatient(id) {
  clearModalValidationErrors();
  const errors = validatePatientForm({ checkEmail: false });
  if (errors.length) return displayModalValidationError(errors);
  const patientRecord = {
    identifiers: collectPatientIdentifiers(),
    nameFamily: document.getElementById("patientFamilyName").value || null,
    nameGiven: document.getElementById("patientGivenName").value || null,
    birthDate: document.getElementById("patientBirthDate").value || null,
    telecomPhone: document.getElementById("patientTelecomPhone").value || null,
  };
  let response = await apiRequest(
    "PATCH",
    `patients/${id}?organizationId=${
      document.getElementById("organizationForPatients")?.value
    }`,
    patientRecord
  );
  if (response.ok && document.getElementById("addOrganizationId")) {
    response = await apiRequest(
      "PATCH",
      `patients/${id}/global_add_organization?organizationId=${
        document.getElementById("addOrganizationId").value
      }`
    );
  }
  if (response.ok) await navReturnFromCrud();
}

async function deletePatient(id) {
  if (
    await apiRequest(
      "DELETE",
      `patients/${id}?organizationId=${
        document.getElementById("organizationForPatients")?.value
      }`
    )
  )
    await navReturnFromCrud();
}

function isValidPatientEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function validatePatientForm({ checkEmail }) {
  const errors = [];
  if (checkEmail) {
    const email =
      document.getElementById("patientTelecomEmail")?.value?.trim() || "";
    if (!email) {
      errors.push("Patient e-mail is required.");
    } else if (!isValidPatientEmail(email)) {
      errors.push("Patient e-mail is not a valid e-mail address.");
    }
  }
  const rows = document.querySelectorAll(
    "#patientIdentifiersContainer .patient-identifier-row"
  );
  let halfFilled = false;
  rows.forEach((row) => {
    const system = row
      .querySelector(".patient-identifier-system")
      ?.value?.trim();
    const value = row
      .querySelector(".patient-identifier-value")
      ?.value?.trim();
    if (!!system !== !!value) halfFilled = true;
  });
  if (halfFilled) {
    errors.push("Each external identifier needs both a System and a Value.");
  }
  return errors;
}

function collectPatientIdentifiers() {
  const rows = document.querySelectorAll(
    "#patientIdentifiersContainer .patient-identifier-row"
  );
  const identifiers = [];
  rows.forEach((row) => {
    const system = row.querySelector(".patient-identifier-system")?.value?.trim();
    const value = row.querySelector(".patient-identifier-value")?.value?.trim();
    if (system && value) identifiers.push({ system, value });
  });
  return identifiers;
}

function renderPatientAddIdentifierInput() {
  const container = document.getElementById("patientIdentifiersContainer");
  if (!container) return;

  const template = Handlebars.compile(`
    <div class="patient-identifier-row d-flex gap-2 mb-2">
      <input type="text" class="form-control patient-identifier-system" placeholder="System" />
      <input type="text" class="form-control patient-identifier-value" placeholder="Value" />
      <button type="button" class="btn btn-outline-danger" onclick="renderPatientRemoveIdentifierInput(this)">
        <i class="bi bi-trash"></i>
      </button>
    </div>
  `);

  container.insertAdjacentHTML("beforeend", template({}));
}

function renderPatientRemoveIdentifierInput(target) {
  if (typeof target === "number") {
    const rows = document.querySelectorAll(
      "#patientIdentifiersContainer .patient-identifier-row"
    );
    if (rows[target]) rows[target].remove();
    return;
  }
  if (target && target.closest) {
    const row = target.closest(".patient-identifier-row");
    if (row) row.remove();
  }
}

async function getInvitationLink(patientId, clientId, sendEmail) {
  const invitationLinkResponse = await apiRequest(
    "POST",
    "invitation",
    { patient_id: patientId, client_id: clientId, send_email: sendEmail }
  );
  const invitationLink = await invitationLinkResponse.json();
  document.getElementById(`invitationLink-${clientId}`).value =
    invitationLink["invitationLink"];
  document.getElementById(`copyInvitationLink-${clientId}`).disabled = false;
}

// ────────────────────────────────────────────────────
// Studies
// ────────────────────────────────────────────────────

async function renderStudies(queryParams) {
  const content = Handlebars.compile(
    document.getElementById("t-studies").innerHTML
  );

  const organizationsResponse = await apiRequest("GET", "users/organizations");
  const organizations = await organizationsResponse.json();

  if (organizations.length == 0) {
    return pageNoticeMarkup("This user does not belong to any Organization.");
  }

  // If no org is selected, lets check what they were last using in the profile,
  // otherwise default to the first organization in the list
  if (!queryParams.organizationId) {
    const currentOrganizationId = (await getUserProfile()).settings?.currentOrganizationId
    if(currentOrganizationId){
      console.log("Setting currentOrganizationId from user profile settings: ", currentOrganizationId)
      queryParams.organizationId = currentOrganizationId;
    } else {
      queryParams.organizationId = organizations[0].id;
    }
  }
  let selectedOrganization;
  const organizationForStudiesSelect = organizations.map((organization) => {
    if (organization.id === parseInt(queryParams.organizationId)) {
      organization.selected = true;
      selectedOrganization = organization;
    } else {
      organization.selected = false;
    }
    return organization;
  });

  const studiesResponse = await apiRequest("GET", "studies", {
    organizationId: queryParams.organizationId,
  });

  const studiesPaginated = await studiesResponse.json();

  let studyRecord, allClients, allDataSources, allScopes;

  if (queryParams.create) {
    studyRecord = {
      organization: {
        id: queryParams.organizationId,
        name: queryParams.organizationName,
      },
    };
  } else if (queryParams.read || queryParams.update || queryParams.delete) {
    const studyRecordResponse = await apiRequest(
      "GET",
      `studies/${queryParams.id}`
    );
    studyRecord = await studyRecordResponse.json();

    if (studyRecord.iconUrl) {
      setTimeout(() => {
        const iconUrlInput = document.getElementById("studyIconUrl");
        if (iconUrlInput) {
          iconUrlInput.value = studyRecord.iconUrl;
          previewIcon(iconUrlInput);
        }
      }, 800);
    }

    if (queryParams.read) {

      // ---- Clients ----
      const studyClientsResponse = await apiRequest(
        "GET",
        `studies/${queryParams.id}/clients`
      );
      studyRecord.clients = await studyClientsResponse.json();

      const allClientsResponse = await apiRequest("GET", `clients`);
      allClients = await allClientsResponse.json();

      // filter out the data sources that have already been added
      const clientIds = studyRecord.clients.map((s) => s.id);
      allClients.results = allClients.results.filter(
        (client) => clientIds.indexOf(client.id) == -1
      );


      const studyDataSourcesResponse = await apiRequest(
        "GET",
        `studies/${queryParams.id}/data_sources`
      );
      studyRecord.dataSources = await studyDataSourcesResponse.json();

      const allDataSourcesResponse = await apiRequest("GET", `data_sources`);
      allDataSources = await allDataSourcesResponse.json();

      // filter out the data sources that have already been added
      const dataSourceIds = studyRecord.dataSources.map((s) => s.id);
      allDataSources.results = allDataSources.results.filter(
        (dataSource) => dataSourceIds.indexOf(dataSource.id) == -1
      );

      const studyScopesRequestedResponse = await apiRequest(
        "GET",
        `studies/${queryParams.id}/scope_requests`
      );
      studyRecord.scopesRequested = await studyScopesRequestedResponse.json();

      const allScopesResponse = await apiRequest(
        "GET",
        `data_sources/all_scopes`
      );
      allScopes = await allScopesResponse.json();

      // filter out the scopes that have already been requested
      const scopesRequestedIds = studyRecord.scopesRequested.map(
        (s) => s.scopeCode.id
      );
      allScopes = allScopes.filter(
        (scope) => scopesRequestedIds.indexOf(scope.id) == -1
      );
    }
  }

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML
  );

  const canManagePractitionersInOrg = await hasOrganizationPermission(
    selectedOrganization,
    queryParams.organizationId,
    "study.manage_for_organization"
  );

  const renderParams = {
    ...queryParams,
    studies: studiesPaginated?.results,
    studyRecord: studyRecord,
    allScopes: allScopes ?? null,
    allClients: allClients?.results ?? null,
    allDataSources: allDataSources?.results ?? null,
    patientCount: store.addPatientIdsToStudy
      ? store.addPatientIdsToStudy.length
      : null,
    organizationForStudiesSelect: organizationForStudiesSelect,
    manageForPractitioners: canManagePractitionersInOrg,
  };

  return content(renderParams);
}

async function createStudyFromOrganization(organizationId, organizationName) {
  if (crudModal._isShown) crudModal.hide();
  nav("studies", {
    create: true,
    organizationId: organizationId,
    organizationName: organizationName,
  });
}

async function createStudy() {
  const studyRecord = {
    name: document.getElementById("studyName").value || null,
    description: document.getElementById("studyDescription").value || null,
    organization: parseInt(
      document.getElementById("studyOrganizationId").value
    ),
    iconUrl: document.getElementById("studyIconUrl").value || null,
  };
  const response = await apiRequest("POST", `studies`, studyRecord);
  if (response.ok) await navReturnFromCrud();
}

async function updateStudy(id) {
  const studyRecord = {
    name: document.getElementById("studyName").value || null,
    description: document.getElementById("studyDescription").value || null,
    iconUrl: document.getElementById("studyIconUrl").value || null,
  };
  const response = await apiRequest("PATCH", `studies/${id}`, studyRecord);
  if (response.ok) await navReturnFromCrud();
}

function getSelectedRecordIds(selector) {
  const selected = [];
  document.querySelectorAll(selector).forEach((checkbox) => {
    if (checkbox.checked) {
      selected.push(parseInt(checkbox.value));
    }
  });
  return selected;
}

async function selectPatientsForStudy(organizationId) {
  const selectedRecordIds = getSelectedRecordIds(".patient-checkbox");
  if (selectedRecordIds.length == 0) {
    displayPageNotice("Please select one or more Patients to add to the Study.");
    return;
  }
  clearPageNotice();
  delete store.addPatientIdsToStudy;
  store.addPatientIdsToStudy = selectedRecordIds;
  nav("studies", { organizationId: organizationId, addPatients: true });
}

async function addPatientsToStudy(studyId, organizationId) {
  const patientUserIdsRecord = {
    patientIds: store.addPatientIdsToStudy,
  };
  const response = await apiRequest(
    "POST",
    `studies/${studyId}/patients`,
    patientUserIdsRecord
  );
  if (response.ok)
    nav("patients", { studyId: studyId, organizationId: organizationId });
}

async function removeSelectedPatientsFromStudy(studyId) {
  const selectedRecordIds = getSelectedRecordIds(".patient-checkbox");
  if (selectedRecordIds.length == 0) {
    displayPageNotice(
      `Please select one or more Patients to remove from Study ${studyId}`
    );
    return;
  }
  clearPageNotice();
  removePatientsFromStudy(selectedRecordIds, studyId);
}

async function removePatientsFromStudy(patientIds, studyId) {
  if (!patientIds || !studyId) return;
  const response = await apiRequest("DELETE", `studies/${studyId}/patients`, {
    patientIds: patientIds,
  });
  if (response.ok) navReload();
}

async function addScopeRequestToStudy(scopeCodeId, studyId) {
  if (!scopeCodeId || !studyId) return;
  const response = await apiRequest(
    "POST",
    `studies/${studyId}/scope_requests`,
    {
      scopeCodeId: scopeCodeId,
    }
  );
  if (response.ok) navReload();
}

async function removeScopeRequestFromStudy(scopeCodeId, studyId) {
  if (!scopeCodeId || !studyId) return;
  const response = await apiRequest(
    "DELETE",
    `studies/${studyId}/scope_requests`,
    {
      scopeCodeId: scopeCodeId,
    }
  );
  if (response.ok) navReload();
}

async function addClientToStudy(clientId, studyId) {
  if (!clientId || !studyId) return;
  const response = await apiRequest("POST", `studies/${studyId}/clients`, {
    clientId: clientId,
  });
  if (response.ok) navReload();
}

async function removeClientFromStudy(clientId, studyId) {
  if (!clientId || !studyId) return;
  const response = await apiRequest(
    "DELETE",
    `studies/${studyId}/clients`,
    {
      clientId: clientId,
    }
  );
  if (response.ok) navReload();
}

async function addDataSourceToStudy(dataSourceId, studyId) {
  if (!dataSourceId || !studyId) return;
  const response = await apiRequest("POST", `studies/${studyId}/data_sources`, {
    dataSourceId: dataSourceId,
  });
  if (response.ok) navReload();
}

async function removeDataSourceFromStudy(dataSourceId, studyId) {
  if (!dataSourceId || !studyId) return;
  const response = await apiRequest(
    "DELETE",
    `studies/${studyId}/data_sources`,
    {
      dataSourceId: dataSourceId,
    }
  );
  if (response.ok) navReload();
}

async function deleteStudy(id) {
  response = await apiRequest("DELETE", `studies/${id}`);
  if (response.ok) await navReturnFromCrud();
}

// ────────────────────────────────────────────────────
// Observations
// ────────────────────────────────────────────────────

async function renderObservations(queryParams) {
  const organizationsResponse = await apiRequest("GET", "users/organizations");
  const organizations = await organizationsResponse.json();

  if (organizations.length == 0) {
    return pageNoticeMarkup("This user does not belong to any Organization.");
  }

  // If no org is selected, lets check what they were last using in the profile,
  // otherwise default to the first organization in the list
  if (!queryParams.organizationId) {
    const currentOrganizationId = (await getUserProfile()).settings
      ?.currentOrganizationId;
    if (currentOrganizationId) {
      console.log(
        "Setting currentOrganizationId from user profile settings: ",
        currentOrganizationId,
      );
      queryParams.organizationId = currentOrganizationId;
    } else {
      queryParams.organizationId = organizations[0].id;
    }
  }
  let selectedOrganization;
  const organizationForObservationsSelect = organizations.map(
    (organization) => {
      if (organization.id === parseInt(queryParams.organizationId)) {
        organization.selected = true;
        selectedOrganization = organization;
      } else {
        organization.selected = false;
      }
      return organization;
    },
  );

  const studiesResponse = await apiRequest("GET", "studies", {
    organizationId: queryParams.organizationId,
  });
  const studies = await studiesResponse.json();

  // Restore the last-used study from settings when arriving without an explicit studyId
  // (e.g. navigating here from another route). If studyId is present as a key — even as
  // null, which "All Studies" passes — skip restoration so the user's choice is honoured.
  const currentStudyId = (await getUserProfile()).settings?.currentStudyId;
  if (currentStudyId && !("studyId" in queryParams)) {
    queryParams.studyId = currentStudyId;
  }

  const selectedStudyId = queryParams.studyId
    ? parseInt(queryParams.studyId)
    : null;
  const studyForObservationsSelect = studies.results.map((study) => {
    const studyIsSelected =
      selectedStudyId !== null && study.id === selectedStudyId;
    study.selected = studyIsSelected;
    return study;
  });

  const content = Handlebars.compile(
    document.getElementById("t-observations").innerHTML,
  );

  // Parse the page and pageSize from queryParams
  const pageParsed = parseInt(queryParams.page) || 1;
  const pageSizeParsed = parseInt(queryParams.pageSize) || 20;

  // Use isNaN to check for invalid numbers, and default to null (or any safe value)
  const observationParams = {
    organizationId: queryParams.organizationId,
    page: isNaN(pageParsed) ? null : pageParsed,
    pageSize: isNaN(pageSizeParsed) ? null : pageSizeParsed,
  };

  if (queryParams.studyId) {
    observationParams["studyId"] = queryParams.studyId;
  }

  const observationsResponse = await apiRequest(
    "GET",
    "observations",
    observationParams,
  );

  const observationsPaginated = await observationsResponse.json();

  const currentPageSize = isNaN(pageSizeParsed) ? 20 : pageSizeParsed;
  if (
    observationsPaginated.results &&
    observationsPaginated.results.length > currentPageSize
  ) {
    observationsPaginated.results = observationsPaginated.results.slice(
      0,
      currentPageSize,
    );
  }

  observationsPaginated.results = observationsPaginated.results.map(
    (observation) => {
      observation.omhData = JSON.stringify(
        observation.omhData,
        null,
        2,
      );
      return observation;
    },
  );

  let observationRecord;

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML,
  );

  Handlebars.registerHelper("eq", function (v1, v2) {
    return v1 === v2;
  });

  const renderParams = {
    ...queryParams,
    observations: observationsPaginated.results,
    observationRecord: observationRecord,
    page: isNaN(pageParsed) ? 1 : pageParsed,
    pageSize: isNaN(pageSizeParsed) ? 20 : pageSizeParsed,
    totalPages: Math.ceil(
      observationsPaginated.count /
        (isNaN(pageSizeParsed) ? 20 : pageSizeParsed),
    ),
    organizationForObservationsSelect: organizationForObservationsSelect,
    studyForObservationsSelect: studyForObservationsSelect,
    pageSizes: [20, 100, 500, 1000],
  };

  return content(renderParams);
}

// ────────────────────────────────────────────────────
// FHIR Resources
// ────────────────────────────────────────────────────

// The patient's full name, read from the JHE provenance extension the server stamps on every
// stored aux resource (see _with_jhe_extensions in core/views/fhir.py).
function fhirPatientName(resource) {
  const extension = (resource.extension || []).find(
    (e) =>
      e.url ===
      "https://jupyterhealth.org/fhir/StructureDefinition/patient-full-name",
  );
  return extension?.valueString || "";
}

async function renderFhir(queryParams) {
  const organizationsResponse = await apiRequest("GET", "users/organizations");
  const organizations = await organizationsResponse.json();

  if (organizations.length == 0) {
    return pageNoticeMarkup("This user does not belong to any Organization.");
  }

  // If no org is selected, lets check what they were last using in the profile,
  // otherwise default to the first organization in the list
  if (!queryParams.organizationId) {
    const currentOrganizationId = (await getUserProfile()).settings
      ?.currentOrganizationId;
    if (currentOrganizationId) {
      console.log(
        "Setting currentOrganizationId from user profile settings: ",
        currentOrganizationId,
      );
      queryParams.organizationId = currentOrganizationId;
    } else {
      queryParams.organizationId = organizations[0].id;
    }
  }
  let selectedOrganization;
  const organizationForFhirSelect = organizations.map((organization) => {
    if (organization.id === parseInt(queryParams.organizationId)) {
      organization.selected = true;
      selectedOrganization = organization;
    } else {
      organization.selected = false;
    }
    return organization;
  });

  const studiesResponse = await apiRequest("GET", "studies", {
    organizationId: queryParams.organizationId,
  });
  const studies = await studiesResponse.json();

  // Restore the last-used study from settings when arriving without an explicit studyId
  // (e.g. navigating here from another route). If studyId is present as a key — even as
  // null, which "All Studies" passes — skip restoration so the user's choice is honoured.
  const currentStudyId = (await getUserProfile()).settings?.currentStudyId;
  if (currentStudyId && !("studyId" in queryParams)) {
    queryParams.studyId = currentStudyId;
  }

  const selectedStudyId = queryParams.studyId
    ? parseInt(queryParams.studyId)
    : null;
  const studyForFhirSelect = studies.results.map((study) => {
    const studyIsSelected =
      selectedStudyId !== null && study.id === selectedStudyId;
    study.selected = studyIsSelected;
    return study;
  });

  // Restore the last-used resource from settings when arriving without an explicit resource
  // (e.g. navigating here from another route), mirroring the organization/study restoration.
  const currentFhirResource = (await getUserProfile()).settings
    ?.currentFhirResource;
  if (currentFhirResource && !("resource" in queryParams)) {
    queryParams.resource = currentFhirResource;
  }

  // The Resource select lists every supported FHIR resource type (from server settings).
  const selectedResource = queryParams.resource || CONSTANTS.FHIR_RESOURCES[0];
  const resourceForFhirSelect = CONSTANTS.FHIR_RESOURCES.map((resource) => ({
    name: resource,
    selected: resource === selectedResource,
  }));

  const content = Handlebars.compile(
    document.getElementById("t-fhir").innerHTML,
  );

  // Parse the page and pageSize from queryParams
  const pageParsed = parseInt(queryParams.page) || 1;
  const pageSizeParsed = parseInt(queryParams.pageSize) || 20;

  // FHIR search uses the canonical location filters and the _page/_count pagination params.
  const fhirQuery = {
    "patient.organization": queryParams.organizationId,
    _page: isNaN(pageParsed) ? 1 : pageParsed,
    _count: isNaN(pageSizeParsed) ? 20 : pageSizeParsed,
  };

  if (queryParams.studyId) {
    fhirQuery["patient._has:Group:member:_id"] = queryParams.studyId;
  }

  const fhirResponse = await apiRequest(
    "GET",
    `FHIR/R5/${selectedResource}`,
    fhirQuery,
  );

  const bundle = await fhirResponse.json();

  const fhir = (bundle.entry || []).map((entry) => {
    const resource = entry.resource;
    return {
      id: resource.id,
      patientName: fhirPatientName(resource),
      fhirData: JSON.stringify(resource, null, 2),
    };
  });

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML,
  );

  Handlebars.registerHelper("eq", function (v1, v2) {
    return v1 === v2;
  });

  const renderParams = {
    ...queryParams,
    fhir: fhir,
    page: isNaN(pageParsed) ? 1 : pageParsed,
    pageSize: isNaN(pageSizeParsed) ? 20 : pageSizeParsed,
    totalPages: Math.ceil(
      bundle.total / (isNaN(pageSizeParsed) ? 20 : pageSizeParsed),
    ),
    organizationForFhirSelect: organizationForFhirSelect,
    studyForFhirSelect: studyForFhirSelect,
    resourceForFhirSelect: resourceForFhirSelect,
    pageSizes: [20, 100, 500, 1000],
  };

  return content(renderParams);
}

// ────────────────────────────────────────────────────
// Clients
// ────────────────────────────────────────────────────

async function renderClients(queryParams) {
  const content = Handlebars.compile(
    document.getElementById("t-clients").innerHTML
  );

  const clientsResponse = await apiRequest("GET", "clients");
  const clientsPaginated = await clientsResponse.json();
  let clientRecord = {};
  let allDataSources;

  if (queryParams.read || queryParams.update || queryParams.delete) {
    const clientRecordResponse = await apiRequest(
      "GET",
      `clients/${queryParams.id}`
    );
    clientRecord = await clientRecordResponse.json();
  }

  if (queryParams.read) {
    const clientDataSourcesResponse = await apiRequest(
      "GET",
      `clients/${queryParams.id}/data_sources`
    );
    clientRecord.dataSources = await clientDataSourcesResponse.json();

    const allDataSourcesResponse = await apiRequest(
      "GET",
      `data_sources`
    );
    allDataSources = (await allDataSourcesResponse.json()).results;

    // filter out already added data sources
    const associatedDataSourceIds = clientRecord.dataSources.map(
      (ds) => ds.id
    );
    allDataSources = allDataSources.filter(
      (dataSource) => associatedDataSourceIds.indexOf(dataSource.id) == -1
    );
    console.log(allDataSources)
  }

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML
  );

  const renderParams = {
    ...queryParams,
    clients: clientsPaginated.results,
    clientRecord: clientRecord,
    allDataSources: allDataSources,
    canManageClients: await hasGlobalPermission("client.manage"),
  };

  return content(renderParams);
}

async function createClient() {
  const clientRecord = {
    name: document.getElementById("clientName").value,
    invitationUrl: document.getElementById("clientInvitationUrl").value,
    clientId: document.getElementById("clientClientId").value
  };
  if (await apiRequest("POST", `clients`, clientRecord))
    await navReturnFromCrud();
}

async function updateClient(id) {
  const clientRecord = {
    name: document.getElementById("clientName").value,
    invitationUrl: document.getElementById("clientInvitationUrl").value,
    clientId: document.getElementById("clientClientId").value,
  };
  const response = await apiRequest("PATCH", `clients/${id}`, clientRecord);
  if (response.ok) await navReturnFromCrud();
}

async function deleteClient(id) {
  if (await apiRequest("DELETE", `clients/${id}`)) await navReturnFromCrud();
}

function generateClientId(length = 40) {
  const alphabet =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  const alphabetLen = alphabet.length; // 62
  const out = [];
  const max = 256 - (256 % alphabetLen); // avoid modulo bias

  while (out.length < length) {
    const buf = new Uint8Array(length);
    crypto.getRandomValues(buf);

    for (let i = 0; i < buf.length && out.length < length; i++) {
      const x = buf[i];
      if (x < max) out.push(alphabet[x % alphabetLen]);
    }
  }
  return out.join("");
}

async function addDataSourceToClient(dataSourceId, clientId) {
  if (!dataSourceId || !clientId) return;
  const response = await apiRequest(
    "POST",
    `clients/${clientId}/data_sources`,
    {
      dataSourceId: dataSourceId,
    }
  );
  if (response.ok) navReload();
}

async function removeDataSourceFromClient(dataSourceId, clientId) {
  if (!dataSourceId || !clientId) return;
  const response = await apiRequest(
    "DELETE",
    `clients/${clientId}/data_sources`,
    {
      dataSourceId: dataSourceId,
    }
  );
  if (response.ok) navReload();
}

// ────────────────────────────────────────────────────
// Data Sources
// ────────────────────────────────────────────────────

async function renderDataSources(queryParams) {
  const content = Handlebars.compile(
    document.getElementById("t-data-sources").innerHTML
  );

  const dataSourcesResponse = await apiRequest("GET", "data_sources");
  const dataSourcesPaginated = await dataSourcesResponse.json();
  let dataSourceRecord = {};
  let allScopes;

  if (queryParams.read || queryParams.update || queryParams.delete) {
    const dataSourceRecordResponse = await apiRequest(
      "GET",
      `data_sources/${queryParams.id}`
    );
    dataSourceRecord = await dataSourceRecordResponse.json();
  }

  dataSourceRecord.typeSelect = buildSelectOptions(CONSTANTS.DATA_SOURCE_TYPES);

  if (queryParams.read) {
    const dataSourceSupportedScopesResponse = await apiRequest(
      "GET",
      `data_sources/${queryParams.id}/supported_scopes`
    );
    dataSourceRecord.supportedScopes =
      await dataSourceSupportedScopesResponse.json();

    const allScopesResponse = await apiRequest(
      "GET",
      `data_sources/all_scopes`
    );
    allScopes = await allScopesResponse.json();

    // filter out the scopes that have already been requested
    const scopesSupportedIds = dataSourceRecord.supportedScopes.map(
      (s) => s.scopeCode.id
    );
    allScopes = allScopes.filter(
      (scope) => scopesSupportedIds.indexOf(scope.id) == -1
    );
  }

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML
  );

  const renderParams = {
    ...queryParams,
    dataSources: dataSourcesPaginated.results,
    dataSourceRecord: dataSourceRecord,
    allScopes: allScopes,
    canManageDataSources: await hasGlobalPermission("data_source.manage"),
  };

  return content(renderParams);
}

async function createDataSource() {
  const dataSourceRecord = {
    name: document.getElementById("dataSourceName").value || null,
    type: document.getElementById("dataSourceType").value,
  };
  if (await apiRequest("POST", `data_sources`, dataSourceRecord))
    await navReturnFromCrud();
}

async function updateDataSource(id) {
  const dataSourceRecord = {
    name: document.getElementById("dataSourceName").value || null,
    type: document.getElementById("dataSourceType").value || null,
  };
  const response = await apiRequest("PATCH", `data_sources/${id}`, dataSourceRecord);
  if (response.ok) await navReturnFromCrud();
}

async function deleteDataSource(id) {
  if (await apiRequest("DELETE", `data_sources/${id}`))
    await navReturnFromCrud();
}

async function addScopeToDataSource(scopeCodeId, dataSourceId) {
  if (!scopeCodeId || !dataSourceId) return;
  const response = await apiRequest(
    "POST",
    `data_sources/${dataSourceId}/supported_scopes`,
    {
      scopeCodeId: scopeCodeId,
    }
  );
  if (response.ok) navReload();
}

async function removeScopeFromDataSource(scopeCodeId, dataSourceId) {
  if (!scopeCodeId || !dataSourceId) return;
  const response = await apiRequest(
    "DELETE",
    `data_sources/${dataSourceId}/supported_scopes`,
    {
      scopeCodeId: scopeCodeId,
    }
  );
  if (response.ok) navReload();
}

// ────────────────────────────────────────────────────
// JheSettings
// ────────────────────────────────────────────────────

async function renderJheSettings(queryParams) {
  const content = Handlebars.compile(
    document.getElementById("t-jhe-settings").innerHTML
  );

  const jheSettingsResponse = await apiRequest("GET", "jhe_settings");
  const jheSettingsPaginated = await jheSettingsResponse.json();
  let jheSettingRecord = {};

  if (queryParams.read || queryParams.update || queryParams.delete) {
    const jheSettingRecordResponse = await apiRequest(
      "GET",
      `jhe_settings/${queryParams.id}`
    );
    jheSettingRecord = await jheSettingRecordResponse.json();
  }

  jheSettingRecord.valueTypeSelect = buildSelectOptions(CONSTANTS.JHE_SETTING_VALUE_TYPES);

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML
  );

  const renderParams = {
    ...queryParams,
    jheSettings: jheSettingsPaginated.results,
    jheSettingRecord: jheSettingRecord,
    canManageJheSettings: true //await hasGlobalPermission("jhe_setting.manage"),
  };

  return content(renderParams);
}

async function createJheSetting() {
  const jheSettingRecord = {
    key: document.getElementById("jheSettingKey").value || null,
    valueType: document.getElementById("jheSettingValueType").value,
    value: document.getElementById("jheSettingValue").value || null,
  };
  if (await apiRequest("POST", `jhe_settings`, jheSettingRecord))
    await navReturnFromCrud();
}

async function updateJheSetting(id) {
  const jheSettingRecord = {
    key: document.getElementById("jheSettingKey").value || null,
    valueType: document.getElementById("jheSettingValueType").value,
    value: document.getElementById("jheSettingValue").value || null,
  };
  const response = await apiRequest("PATCH", `jhe_settings/${id}`, jheSettingRecord);
  if (response.ok) await navReturnFromCrud();
}

async function deleteJheSetting(id) {
  if (await apiRequest("DELETE", `jhe_settings/${id}`)) await navReturnFromCrud();
}

// ────────────────────────────────────────────────────
// Dev and Debug
// ────────────────────────────────────────────────────

// add an event listener to the window that watches for url changes
// window.onpopstate = locationHandler;
// call the urlLocationHandler function to handle the initial url
// window.route = route;
// call the urlLocationHandler function to handle the initial url
// locationHandler();

function renderDebug() {
  const content = Handlebars.compile(
    document.getElementById("t-debug").innerHTML,
  );
  return content({});
}

function debugGetUser() {
  userManager
    .getUser()
    .then((user) => {
      document.getElementById("debugAuthOut").innerHTML =
        "userManager: " + JSON.stringify(user, null, 2);
    })
    .catch((err) => {
      console.error(err);
    });
}

function debugRemoveUser() {
  userManager
    .removeUser()
    .then(() => {
      document.getElementById("debugAuthOut").innerHTML =
        "userManager: user removed";
    })
    .catch((err) => {
      console.error(err);
    });
}

function debugRedirectSignin() {
  userManager
    .signinRedirect()
    .then((user) => {
      document.getElementById("debugAuthOut").innerHTML =
        "userManager: " + JSON.stringify(user, null, 2);
    })
    .catch((err) => {
      console.error(err);
    });
}

let debugPatientToken;
let debugErrorTimer;

function hideDebugError() {
  const el = document.getElementById("debugErrorMessage");
  if (!el) return;
  el.style.display = "none";
  el.textContent = "";
}

function showDebugError(message) {
  const el = document.getElementById("debugErrorMessage");
  if (!el) return;
  if (!message) {
    hideDebugError();
    return;
  }
  el.textContent = message;
  el.style.display = "block";
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  if (debugErrorTimer) {
    clearTimeout(debugErrorTimer);
  }
  debugErrorTimer = setTimeout(() => {
    hideDebugError();
    debugErrorTimer = null;
  }, 8000);
}

function normalizeHtmlError(html) {
  if (typeof DOMParser === "undefined") return null;
  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    if (!doc) return null;

    const textValue = (selector) =>
      doc.querySelector(selector)?.textContent?.trim().replace(/\s+/g, " ");

    const parts = [
      textValue("title"),
      textValue("h1"),
      textValue("pre.exception_value"),
    ].filter(Boolean);
    if (parts.length) {
      return parts.join(" — ");
    }

    const bodyText = doc.body?.textContent?.trim();
    if (bodyText) {
      return bodyText.replace(/\s+/g, " ").slice(0, 800);
    }
  } catch (err) {
    console.warn("Failed to parse HTML error payload", err);
  }
  return null;
}

function isHtmlPayload(value) {
  return (
    typeof value === "string" &&
    value.trim().startsWith("<") &&
    /<\/?html/i.test(value)
  );
}

async function readResponsePayload(response) {
  const text = await response.text();
  if (!text) {
    return null;
  }
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}

async function debugGetPatientTokenFromCode() {
  showDebugError();
  try {

    const invitationCode = document.getElementById(
      "debugPasteInvitationCode",
    ).value;

    // Split on _"
    const { host, token } = parseInvitationCode(invitationCode);

    const invitationResponse = await fetch(`http://${host}${API_PATH}/invitation/${token}`, {
      method: "POST",
      headers: {
        "Cache-Control": "no-cache",
      },
    });

    const grant = await readResponsePayload(invitationResponse);

    document.getElementById("debugGrantPayload").value = JSON.stringify(
      grant,
      null,
      2,
    );

    grant.grant["code_verifier"] = btoa(token).replace(/=/g, '');

    const formData = new URLSearchParams(grant.grant).toString();
    console.log("Exchanging code for token with form data: ", formData);
    const response = await fetch(grant.token_endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
      },
      body: formData,
    });
    const tokens = await readResponsePayload(response);
    if (!response.ok) {
      showDebugError(
        getFormattedError(tokens, `${response.status} ${response.statusText}`)
      );
      return;
    }
    if (typeof tokens !== "object") {
      showDebugError(`Expected JSON token response but got: ${tokens}`);
      return;
    }
    setDebugPatientToken(tokens?.access_token);
    document.getElementById("debugPatientTokenOut").innerHTML = JSON.stringify(
      tokens,
      null,
      2
    );
  } catch (error) {
    showDebugError(error.message || "Failed to fetch token");
  }
}

function getFormattedError(payload, fallback) {
  if (!payload) return fallback;
  if (typeof payload === "string") {
    if (isHtmlPayload(payload)) {
      const htmlSummary = normalizeHtmlError(payload);
      if (htmlSummary) {
        return htmlSummary;
      }
    }
    const trimmed = payload.trim();
    return trimmed || fallback;
  }
  if (payload.error_description) return payload.error_description;
  if (payload.detail) return payload.detail;
  if (payload.message) return payload.message;
  return JSON.stringify(payload);
}

function getDebugPatientToken(accessToken) {
  if (accessToken) {
    debugPatientToken = accessToken;
    document.getElementById("debugPatientToken").innerHTML =
      `Client Token: ${debugPatientToken}`;
  } else {
    debugPatientToken = null;
    document.getElementById("debugPatientToken").innerHTML =
      `Client Token: None`;
  }
}

function setDebugPatientToken(accessToken) {
  if (accessToken) {
    debugPatientToken = accessToken;
    document.getElementById(
      "debugPatientToken"
    ).innerHTML = `Client Token: ${debugPatientToken}`;
  } else {
    debugPatientToken = null;
    document.getElementById(
      "debugPatientToken"
    ).innerHTML = `Client Token: None`;
  }
}

async function debugGetUserProfile() {
  showDebugError();
  try {
    const response = await fetch(`${API_PATH}/users/profile`, {
      headers: {
        "Cache-Control": "no-cache",
        Authorization: `Bearer ${debugPatientToken}`,
      },
    });
    const out = await readResponsePayload(response);
    if (!response.ok) {
      showDebugError(
        getFormattedError(out, `${response.status} ${response.statusText}`)
      );
      return;
    }
    document.getElementById("debugUserProfileOut").innerHTML = JSON.stringify(
      out,
      null,
      2
    );
  } catch (error) {
    showDebugError(error.message || "Failed to fetch profile");
  }
}

async function debugGetPendingPatientConsents() {
  showDebugError();
  try {
    const response = await fetch(
      document.getElementById("debugPendingPatientConsentsUrl").value,
      {
        headers: {
          "Cache-Control": "no-cache",
          Authorization: `Bearer ${debugPatientToken}`,
        },
      }
    );
    const out = await readResponsePayload(response);
    if (!response.ok) {
      showDebugError(
        getFormattedError(out, `${response.status} ${response.statusText}`)
      );
      return;
    }
    document.getElementById("debugPendingPatientConsentsOut").innerHTML =
      JSON.stringify(out, null, 2);
  } catch (error) {
    showDebugError(error.message || "Failed to fetch consents");
  }
}

async function debugDoPatientConsents() {
  showDebugError();
  const method = document.getElementById("debugPatientConsentsMethod").value;
  const headers = {
    "Cache-Control": "no-cache",
    Authorization: `Bearer ${debugPatientToken}`,
  };
  let body;
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    body = document.getElementById("debugPatientConsentsPayload").value;
  }
  try {
    const response = await fetch(
      document.getElementById("debugPatientConsentsUrl").value,
      {
        method: method,
        headers: headers,
        body: body,
      }
    );
    const out = await readResponsePayload(response);
    if (!response.ok) {
      showDebugError(
        getFormattedError(out, `${response.status} ${response.statusText}`)
      );
      return;
    }
    document.getElementById("debugPatientConsentsOut").innerHTML =
      JSON.stringify(out, null, 2);
  } catch (error) {
    showDebugError(error.message || "Failed to save consents");
  }
}

async function debugDoObservations() {
  showDebugError();
  const method = document.getElementById("debugObservationsMethod").value;
  const headers = {
    "Cache-Control": "no-cache",
  };
  let body;
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    body = document.getElementById("debugObservationsPayload").value;
  }
  try {
    const response = await fetch(
      document.getElementById("debugObservationsUrl").value,
      {
        method: method,
        headers: headers,
        body: body,
      }
    );
    const out = await readResponsePayload(response);
    if (!response.ok) {
      showDebugError(
        getFormattedError(out, `${response.status} ${response.statusText}`)
      );
      return;
    }
    document.getElementById("debugObservationsOut").innerHTML = JSON.stringify(
      out,
      null,
      2
    );
  } catch (error) {
    showDebugError(error.message || "Failed to fetch observations");
  }
}

let iconPreviewTimeout;

function previewIcon(input) {
  if (iconPreviewTimeout) {
    clearTimeout(iconPreviewTimeout);
  }

  const previewContainer = document.getElementById("iconPreview");
  const url = input.value.trim();

  previewContainer.innerHTML = "";
  clearModalValidationErrors();

  if (!url) {
    previewContainer.innerHTML = `
      <div class="text-center text-muted" style="height: 100%; line-height: 46px;">
        <i class="bi bi-image"></i>
      </div>`;
    return;
  }

  previewContainer.innerHTML = `
    <div class="text-center text-muted" style="height: 100%; line-height: 46px;">
      <i class="bi bi-arrow-repeat"></i>
    </div>`;

  iconPreviewTimeout = setTimeout(() => {
    const img = document.createElement("img");
    img.src = url;
    img.alt = "Icon";
    img.style.cssText = "width: 100%; height: 100%; object-fit: cover;";

    const errorDiv = document.createElement("div");
    errorDiv.className = "text-center text-muted";
    errorDiv.style.cssText = "display: none; height: 100%; line-height: 46px;";
    errorDiv.innerHTML = '<i class="bi bi-exclamation-triangle"></i>';

    img.onerror = () => {
      img.style.display = "none";
      errorDiv.style.display = "block";
      displayModalValidationError([
        "Unable to load image from URL. Please check the URL and try again.",
      ]);
    };

    img.onload = () => {
      errorDiv.style.display = "none";
      clearModalValidationErrors();
    };

    previewContainer.innerHTML = "";
    previewContainer.appendChild(img);
    previewContainer.appendChild(errorDiv);
  }, 400);
}
