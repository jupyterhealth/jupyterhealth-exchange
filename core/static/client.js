// ==================================================
// Global Constants
// ==================================================

const ROUTE_PREFIX = "/portal/";
const DEFAULT_ROUTE = "organizations";
const API_PATH = "/api/v1/";
const SITE_BASE_URL =
  (typeof CONSTANTS === "object" && CONSTANTS?.SITE_URL)
    ? CONSTANTS.SITE_URL.replace(/\/$/, "")
    : "";

const buildTokenEndpoint = () => `${SITE_BASE_URL}/o/token/`;
const buildApiUrl = (resource) => `${SITE_BASE_URL}${API_PATH}${resource}`;
const buildTokenPayload = (code = "PASTE_CODE_HERE") => ({
  code: code,
  grant_type: "authorization_code",
  redirect_uri: `${SITE_BASE_URL}/auth/callback`,
  client_id: CONSTANTS.client_id,
  code_verifier: CONSTANTS.code_verifier,
  code_challenge: CONSTANTS.code_challenge,
});
const buildPatientConsentsUrl = (patientId = "PASTE_PATIENT_ID_HERE") =>
  buildApiUrl(`patients/${patientId}/consents`);

const ROUTES = {
  // dashboard: {
  //   label: "Dashboard",
  //   iconClass: "bi-speedometer",
  //   action: "renderDashboard",
  // },
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
    label: "Observations",
    iconClass: "bi-database",
    action: "renderObservations",
  },
  dataSources: {
    label: "Data Sources",
    iconClass: "bi-phone",
    action: "renderDataSources",
  },
  debug: {
    label: "Debug",
    iconClass: "bi-bug",
    action: "renderDebug",
  },
};

// ==================================================
// Global Vars
// ==================================================

const actions = {
  renderOrganizations,
  renderPatients,
  renderStudies,
  renderObservations,
  renderDataSources,
  renderDebug,
};
let crudModal;
let store = {};
let userProfile = {};
let signingOut = false;
let showDelayedElementsTimeoutId = null;
let navLoadingOverlayCounter = 0;

// ==================================================
// Common
// ==================================================

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
    const newRouteSettings = ROUTES[newRoute];
    const current = getCurrentRouteAndParams();

    if (!queryParams) {
      queryParams = appendQueryParams
        ? { ...current.params, ...appendQueryParams }
        : {};
    }

    const bodyTpl = Handlebars.compile(
      document.getElementById("t-body").innerHTML
    );

    // Ensure user is authenticated
    if (!(await userManager.getUser())) {
      await userManager.signinRedirect();
    }

    // Render main content for the route
    const mainContent = await actions[newRouteSettings.action](queryParams);

    // Remove any existing main content
    const oldMain = document.getElementById("mainContent");
    if (oldMain) oldMain.remove();

    // Build nav items
    const navItems = Object.entries(ROUTES).map(([route, settings]) => ({
      ...settings,
      active: route === newRoute,
      route,
    }));

    // Replace body content
    const baseBody = document.getElementById("baseBody");
    document
      .querySelectorAll("#baseBody > main")
      .forEach((child) => baseBody.removeChild(child));

    document.getElementById("baseBody").insertAdjacentHTML(
      "afterbegin",
      bodyTpl({ navItems, mainContent })
    );

    // Post-render hooks
    renderUserProfile();
    document.getElementById("jheVersion").textContent = CONSTANTS.JHE_VERSION;

    const crudModalElement = document.getElementById(`${newRoute}-crudModal`);
    if (crudModalElement) {
      crudModal = new bootstrap.Modal(crudModalElement, {});

      // avoid duplicate listeners on re-render
      if (!crudModalElement.dataset.shownHookBound) {
        crudModalElement.dataset.shownHookBound = '1';
        crudModalElement.addEventListener('shown.bs.modal', () => {
          window.MODAL_SHOWN_HANDLERS?.[newRoute]?.(crudModalElement);
        });
      }
    }


    if (queryParams.create || queryParams.read || queryParams.update || queryParams.delete) {
      crudModal.show();
    }

    // Push history if route/params changed
    if (newRoute !== current.route || !isShallowEq(queryParams, current.params)) {
      window.history.pushState(
        {},
        "",
        ROUTE_PREFIX + newRoute + "?" + new URLSearchParams(queryParams).toString()
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
  console.log("popstate", JSON.stringify(event));
  if (!signingOut) navReload(); // see signOut() for explanation
});

async function navReturnFromCrud()  {
  const currentRouteAndParams = getCurrentRouteAndParams();
  const params = currentRouteAndParams.params;
  delete params.create;
  delete params.read;
  delete params.update;
  delete params.delete;
  crudModal.hide(); // This returns immediately but kicks of an async process
  await new Promise(resolve => setTimeout(resolve, 600)); // wait for modal to hide (300 ms)
  nav(currentRouteAndParams.route, params);
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

async function apiRequest(method, resourcePath, params) {
  console.log(
    `apiRequest: ${method} ${resourcePath} ${JSON.stringify(params)}`
  );
  const headers = {
    "Cache-Control": "no-cache",
  };
  const user = await userManager.getUser();
  if (user) headers["Authorization"] = `Bearer ${user.access_token}`;
  let url = API_PATH + resourcePath;
  let body;
  if (params) {
    if (method === "GET") {
      url = `${url}?${new URLSearchParams(params).toString()}`;
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(params);
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
      await userManager.signinRedirect();
      return;
    } else if (parseInt(response.status) == 400) {
      displayModalValidationError(await response.json());
      return;
    } else if (parseInt(response.status) > 299) {
      displayError(response.statusText);
      return;
    }
  } catch (error) {
    displayError(error);
    console.log(`apiRequest Error: ${error}`);
  }
  return response;
}

function renderDebug(param) {
  const content = Handlebars.compile(
    document.getElementById("t-debug").innerHTML
  );
  setTimeout(() => {
    const payloadElement = document.getElementById("debugOAuthPayload");
    if (payloadElement) {
      payloadElement.value = JSON.stringify(buildTokenPayload(), null, 2);
    }

    const consentsUrlElement = document.getElementById("debugPatientConsentsUrl");
    if (consentsUrlElement) {
      consentsUrlElement.value = buildPatientConsentsUrl();
    }

    const tokenEndpointLabel = document.getElementById("debugTokenEndpointLabel");
    if (tokenEndpointLabel) {
      tokenEndpointLabel.textContent = `POST ${buildTokenEndpoint()}`;
    }

    const userProfileEndpointLabel = document.getElementById("debugUserProfileEndpointLabel");
    if (userProfileEndpointLabel) {
      userProfileEndpointLabel.textContent = `GET ${buildApiUrl("users/profile")}`;
    }
  }, 200);
  return content({});
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



async function hasOrgPermission(selectedOrganization, organizationId, permission) {
  let role = selectedOrganization?.currentUserRole;

  if (!role && organizationId) {
    try {
      const orgRes = await apiRequest("GET", `organizations/${organizationId}`);
      const org = await orgRes.json();
      role = org?.currentUserRole;
    } catch (_) {
      return false;
    }
  }

  return role ? ifRoleCan(role, permission) : false;
}


async function hasGlobalPermission(permission) {
  if (!userProfile || !userProfile.email) {
    try {
      userProfile = await getUserProfile();
    } catch (_) {
      return false;
    }
  }
  return userProfile.isSuperuser;
}



// ==================================================
// User Profile
// ==================================================

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

/**
 * userManager.removeUser() raises an event which triggers
 * the popstate listner which calls navReload to catch back
 * button events. Skip this for signout otherwise redirect
 * is never processed.
 */
async function signOut() {
  signingOut = true;
  await userManager.removeUser();
  this.document.location = "/accounts/logout";
}

// ────────────────────────────────────────────────────
// Permission helper (must appear before any render*())
// ────────────────────────────────────────────────────
function ifRoleCan(role, permission) {
  return (window.ROLE_PERMISSIONS[role] || []).includes(permission)
}

// ==================================================
// Organizations
// ==================================================

async function renderOrganizations(queryParams) {
  console.log(`queryParams: ${JSON.stringify(queryParams)}`);
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
        'organization.manage_for_practitioners'
      );
    }


    const organizationTreeResaponse = await apiRequest(
      "GET",
      `organizations/${queryParams.tloId}/tree`
    );
    const organizationTree = await organizationTreeResaponse.json();
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
        'organization.manage_for_practitioners'
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

  const canCreateTopLevelOrg = await hasGlobalPermission("");


  const renderParams = {
    ...queryParams,
    topLevelOrganizationsSelect: topLevelOrganizationsSelect,
    children: organizationTreeChildren,
    organizationRecord: organizationRecord,
    manageForPractitioners: canManagePractitionersInOrg,
    canCreateTopLevelOrg: canCreateTopLevelOrg,
  };

  return content(renderParams);
}



async function createOrganization(partOf) {
  const organizationName =
    document.getElementById("organizationName").value || null;
  const organizationType = document.getElementById("organizationType").value;
  const organizationRecord = {
    name: organizationName,
    type: organizationType,
    partOf: partOf,
  };
  console.log(`organizationRecord: ${JSON.stringify(organizationRecord)}`);
  const response = await apiRequest(
    "POST",
    "organizations",
    organizationRecord
  );
  if (response.ok) await navReturnFromCrud() ;
}

async function updateOrganization(id) {
  const organizationName =
    document.getElementById("organizationName").value || null;
  const organizationType = document.getElementById("organizationType").value;
  const organizationRecord = {
    name: organizationName,
    type: organizationType,
  };
  console.log(`organizationRecord: ${JSON.stringify(organizationRecord)}`);
  const response = await apiRequest(
    "PATCH",
    `organizations/${id}`,
    organizationRecord
  );
  if (response.ok) await navReturnFromCrud() ;
}

async function deleteOrganization(id) {
  const response = await apiRequest("DELETE", `organizations/${id}`);
  if (response.ok) await navReturnFromCrud() ;
}

    async function addUserToOrganization(userEmail, organizationId, role) {
  if (!userEmail || !organizationId) return;
  const userRecordResponse = await apiRequest("GET", "users/search_by_email", {
    email: userEmail,
  });
  const userRecordPaginated = await userRecordResponse.json();
  if (userRecordPaginated.id === undefined) {
    alert("No User with this E-mail exists.");
    return;
  }
  const response = await apiRequest(
    "POST",
    `organizations/${organizationId}/user`,
    {
      jheUserId: userRecordPaginated.id,
      organizationPartitionerRole: role
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

// ==================================================
// Patients
// ==================================================

async function renderPatients(queryParams) {
  console.log(`queryParams: ${JSON.stringify(queryParams)}`);

  const organizationsResponse = await apiRequest("GET", "users/organizations");
  const organizations = await organizationsResponse.json();

  if (organizations.length == 0) {
    alert("This user does not belong to any Organization.");
    return;
  }

  if (!queryParams.organizationId && organizations[0]) {
    nav("patients", { organizationId: organizations[0].id });
    return;
  }

  let selectedOrganization;

  const organizationForPatientsSelect = organizations.map((organization) => {
    if(organization.id === parseInt(queryParams.organizationId)){
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

  const studyForPatientsSelect = studies.results.map((study) => {
    study.selected = study.id === parseInt(queryParams.studyId);
    return study;
  });

  const content = Handlebars.compile(
    document.getElementById("t-patients").innerHTML
  );

  let patientsPaginated, patientRecord, studiesPendingConsent, studiesConsented;

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
      `patients/${queryParams.id}`
    );
    patientRecord = await patientRecordResponse.json();

    if (queryParams.read) {
      const patientRecordConsentsResponse = await apiRequest(
        "GET",
        `patients/${queryParams.id}/consents`
      );
      patientRecordConsents = await patientRecordConsentsResponse.json();
      studiesPendingConsent = patientRecordConsents.studiesPendingConsent;
      studiesConsented = patientRecordConsents.studies;
      console.log(JSON.stringify(patientRecordConsents));
    }
  } else if (queryParams.create && queryParams.lookedUpEmail) {
    patientRecord = {
      telecomEmail: queryParams.lookedUpEmail
    };
  }

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML
  );

  Handlebars.registerHelper("eq", function (v1, v2) {
    return v1 === v2;
  });

  const canManagePractitionersInOrg = await hasOrgPermission(
    selectedOrganization,
    queryParams.organizationId,
    "patient.manage_for_organization"
  );

  const renderParams = {
    ...queryParams,
    patients: patientsPaginated?.results,
    patientRecord: patientRecord,
    hidePatientDetails: (queryParams.create && !queryParams.lookedUpEmail),
    page: page,
    pageSize: pageSize,
    totalPages: Math.ceil(patientsPaginated.count / pageSize),
    organizationForPatientsSelect: organizationForPatientsSelect,
    selectedOrganization: selectedOrganization,
    studyForPatientsSelect: studyForPatientsSelect,
    studiesPendingConsent: studiesPendingConsent,
    studiesConsented: studiesConsented,
    pageSizes: [20, 100, 500, 1000],
    manageForPractitioners: canManagePractitionersInOrg,
  };

  return content(renderParams);
}

async function globalLookupPatientByEmail(email, organizationId) {
  const patientRecordResponse = await apiRequest(
    "GET",
    `patients/global_lookup`,
    {email: email}
  );
  const patientRecord = await patientRecordResponse.json();
  if (patientRecord && patientRecord[0] && patientRecord[0].organizations && patientRecord[0].organizations.length>0 ) {
    const matchingOrganization = patientRecord[0].organizations.find(
      (org) => org.id === organizationId
    );
    if(matchingOrganization){
      return alert(`Patient with E-mail ${email} is already a member of ${matchingOrganization.name}`);
    }
    await navReturnFromCrud() ;
    await nav("patients", {
      update: true,
      id: patientRecord[0].id,
      organizationId: organizationId,
      addOrganizationId: true,
    });
  } else {
    await navReturnFromCrud() ;
    await nav("patients", { create: true, organizationId: organizationId, lookedUpEmail: email });
  }
}

async function createPatient(organizationId) {
  const patientRecord = {
    organizationId: organizationId,
    identifier:
      document.getElementById("patientIdentifier").value || null,
    nameFamily: document.getElementById("patientFamilyName").value || null,
    nameGiven: document.getElementById("patientGivenName").value || null,
    birthDate: document.getElementById("patientBirthDate").value || null,
    telecomEmail: document.getElementById("patientTelecomEmail").value || null,
    telecomPhone: document.getElementById("patientTelecomPhone").value || null,
  };
  const response = await apiRequest("POST", `patients`, patientRecord);
  if (response.ok) await navReturnFromCrud() ;
}

async function updatePatient(id) {
  const patientRecord = {
    identifier: document.getElementById("patientIdentifier").value || null,
    nameFamily: document.getElementById("patientFamilyName").value || null,
    nameGiven: document.getElementById("patientGivenName").value || null,
    birthDate: document.getElementById("patientBirthDate").value || null,
    telecomPhone: document.getElementById("patientTelecomPhone").value || null
  };
  let response = await apiRequest("PATCH", `patients/${id}?organizationId=${document.getElementById('organizationForPatients')?.value}`, patientRecord);
  if(response.ok && document.getElementById("addOrganizationId")){
    response = await apiRequest(
      "PATCH",
      `patients/${id}/global_add_organization?organizationId=${document.getElementById("addOrganizationId").value}`
    );
  }
  if (response.ok) await navReturnFromCrud() ;
}

async function deletePatient(id) {
  if (await apiRequest("DELETE", `patients/${id}?organizationId=${document.getElementById('organizationForPatients')?.value}`)) await navReturnFromCrud() ;
}

async function getInvitationLink(id, sendEmail) {
  const invitationLinkResponse = await apiRequest(
    "GET",
    `patients/${id}/invitation_link?send_email=${sendEmail}`
  );
  const invitationLink = await invitationLinkResponse.json();
  document.getElementById("invitationLink").value =
    invitationLink["invitationLink"];
  document.getElementById("copyInvitationLink").disabled = false;
}

// ==================================================
// Studies
// ==================================================

async function renderStudies(queryParams) {
  console.log(`queryParams: ${JSON.stringify(queryParams)}`);

  const content = Handlebars.compile(
    document.getElementById("t-studies").innerHTML
  );

  const organizationsResponse = await apiRequest("GET", "users/organizations");
  const organizations = await organizationsResponse.json();

  if (organizations.length == 0) {
    alert("This user does not belong to any Organization.");
    return;
  }

  if (!queryParams.organizationId && organizations[0]) {
    nav("studies", { organizationId: organizations[0].id });
    return;
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

  let studyRecord, allDataSources, allScopes;

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

      console.log(studyRecord.dataSources);

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

  const canManagePractitionersInOrg = await hasOrgPermission(
    selectedOrganization,
    queryParams.organizationId,
    "study.manage_for_organization"
  );

  const renderParams = {
    ...queryParams,
    studies: studiesPaginated?.results,
    studyRecord: studyRecord,
    allScopes: allScopes ? allScopes : null,
    allDataSources: allDataSources?.results ? allDataSources.results : null,
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
  if (response.ok) await navReturnFromCrud() ;
}

async function updateStudy(id) {
  const studyRecord = {
    name: document.getElementById("studyName").value || null,
    description: document.getElementById("studyDescription").value || null,
    iconUrl: document.getElementById("studyIconUrl").value || null,
  };
  const response = await apiRequest("PATCH", `studies/${id}`, studyRecord);
  if (response.ok) await navReturnFromCrud() ;
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
    alert("Please select one or more Patients to add to the Study.");
    return;
  }
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
    alert(`Please select one or more Patients to remove from Study ${studyId}`);
    return;
  }
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
  if (response.ok) await navReturnFromCrud() ;
}

// ==================================================
// Observations
// ==================================================

async function renderObservations(queryParams) {
  console.log(`queryParams: ${JSON.stringify(queryParams)}`);

  const organizationsResponse = await apiRequest("GET", "users/organizations");
  const organizations = await organizationsResponse.json();

  if (organizations.length == 0) {
    alert("This user does not belong to any Organization.");
    return;
  }

  if (!queryParams.organizationId && organizations[0]) {
    nav("observations", { organizationId: organizations[0].id });
    return;
  }

  const organizationForObservationsSelect = organizations.map(
    (organization) => {
      organization.selected =
        organization.id === parseInt(queryParams.organizationId);
      return organization;
    }
  );

  const studiesResponse = await apiRequest("GET", "studies", {
    organizationId: queryParams.organizationId,
  });
  const studies = await studiesResponse.json();

  const studyForObservationsSelect = studies.results.map((study) => {
    study.selected = study.id === parseInt(queryParams.studyId);
    return study;
  });

  const content = Handlebars.compile(
    document.getElementById("t-observations").innerHTML
  );

  // Parse the page and pageSize from queryParams
  const pageParsed = parseInt(queryParams.page);
  const pageSizeParsed = parseInt(queryParams.pageSize);

  console.log(`isNaN(pageParsed): ${isNaN(pageParsed)}`);
  console.log(`isNaN(pageSizeParsed): ${isNaN(pageSizeParsed)}`);

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
    observationParams
  );

  const observationsPaginated = await observationsResponse.json();

  const currentPageSize = isNaN(pageSizeParsed) ? 20 : pageSizeParsed;
  if (
    observationsPaginated.results &&
    observationsPaginated.results.length > currentPageSize
  ) {
    observationsPaginated.results = observationsPaginated.results.slice(
      0,
      currentPageSize
    );
  }

  observationsPaginated.results = observationsPaginated.results.map(
    (observation) => {
      observation.valueAttachmentData = JSON.stringify(
        observation.valueAttachmentData,
        null,
        2
      );
      return observation;
    }
  );

  let observationRecord;

  Handlebars.registerPartial(
    "crudButton",
    document.getElementById("t-crudButton").innerHTML
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
        (isNaN(pageSizeParsed) ? 20 : pageSizeParsed)
    ),
    organizationForObservationsSelect: organizationForObservationsSelect,
    studyForObservationsSelect: studyForObservationsSelect,
    pageSizes: [20, 100, 500, 1000],
  };

  return content(renderParams);
}

// ==================================================
// Data Sources
// ==================================================

async function renderDataSources(queryParams) {
  console.log(`queryParams: ${JSON.stringify(queryParams)}`);
  const content = Handlebars.compile(
    document.getElementById("t-dataSources").innerHTML
  );

   const canManagePractitionersInOrg = await hasGlobalPermission("data_source.manage");


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
    manageForPractitioners: canManagePractitionersInOrg,
  };

  return content(renderParams);
}

async function createDataSource() {
  const dataSourceRecord = {
    name: document.getElementById("dataSourceName").value || null,
    type: document.getElementById("dataSourceType").value,
  };
  if (await apiRequest("POST", `data_sources`, dataSourceRecord))
    await navReturnFromCrud() ;
}

async function updateDataSource(id) {
    const studyRecord = {
    name: document.getElementById("dataSourceName").value || null,
    type: document.getElementById("dataSourceType").value || null,
  };
  const response = await apiRequest("PATCH", `data_sources/${id}`, studyRecord);
  if (response.ok) await navReturnFromCrud() ;

}

async function deleteDataSource(id) {
  if (await apiRequest("DELETE", `data_sources/${id}`)) await navReturnFromCrud() ;
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

// ==================================================
// Dev and Debug
// ==================================================

// add an event listener to the window that watches for url changes
// window.onpopstate = locationHandler;
// call the urlLocationHandler function to handle the initial url
// window.route = route;
// call the urlLocationHandler function to handle the initial url
// locationHandler();

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

async function debugGetPatientTokenFromCode() {
  const formData = new URLSearchParams(
    JSON.parse(document.getElementById("debugOAuthPayload").value)
  ).toString();
  const response = await fetch(buildTokenEndpoint(), {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "Cache-Control": "no-cache",
    },
    body: formData,
  });
  const tokens = await response.json();
  setDebugPatientToken(tokens?.access_token);
  document.getElementById("debugPatientTokenOut").innerHTML = JSON.stringify(
    tokens,
    null,
    2
  );
}

function setDebugPatientToken(accessToken) {
  if (accessToken) {
    debugPatientToken = accessToken;
    document.getElementById(
      "debugPatientToken"
    ).innerHTML = `Client Token: ${debugPatientToken}`;
  } else {
    document.getElementById(
      "debugPatientToken"
    ).innerHTML = `Client Token: None`;
  }
}

async function debugGetUserProfile() {
  const response = await fetch(buildApiUrl("users/profile"), {
    headers: {
      "Cache-Control": "no-cache",
      Authorization: `Bearer ${debugPatientToken}`,
    },
  });
  const out = await response.json();
  document.getElementById("debugUserProfileOut").innerHTML = JSON.stringify(
    out,
    null,
    2
  );
}

async function debugGetPendingPatientConsents() {
  const response = await fetch(
    document.getElementById("debugPendingPatientConsentsUrl").value,
    {
      headers: {
        "Cache-Control": "no-cache",
        Authorization: `Bearer ${debugPatientToken}`,
      },
    }
  );
  const out = await response.json();
  document.getElementById("debugPendingPatientConsentsOut").innerHTML =
    JSON.stringify(out, null, 2);
}

async function debugDoPatientConsents() {
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
  const response = await fetch(
    document.getElementById("debugPatientConsentsUrl").value,
    {
      method: method,
      headers: headers,
      body: body,
    }
  );
  const out = await response.json();
  document.getElementById("debugPatientConsentsOut").innerHTML = JSON.stringify(
    out,
    null,
    2
  );
}

async function debugDoObservations() {
  const method = document.getElementById("debugObservationsMethod").value;
  const headers = {
    "Cache-Control": "no-cache",
  };
  let body;
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    body = document.getElementById("debugObservationsPayload").value;
  }
  const response = await fetch(
    document.getElementById("debugObservationsUrl").value,
    {
      method: method,
      headers: headers,
      body: body,
    }
  );
  const out = await response.json();
  document.getElementById("debugObservationsOut").innerHTML = JSON.stringify(
    out,
    null,
    2
  );
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
