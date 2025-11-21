{% autoescape off %}

window.OIDCSettings = {
  authority: "{{ OIDC_CLIENT_AUTHORITY }}",
  client_id: "{{ OIDC_CLIENT_ID }}",
  // silentRequestTimeoutInSeconds: 600,
  // popup_redirect_uri: 'http://127.0.0.1:8000/auth/callback_popup',
  redirect_uri: "{{ OIDC_CLIENT_REDIRECT_URI }}",
  extraQueryParams: {},
  response_mode: "query",
};

const CONSTANTS = {
  JHE_VERSION: "{{ JHE_VERSION }}",
  SITE_URL: "{{ SITE_URL }}",
  client_id: "{{ OIDC_CLIENT_ID }}",
  code_verifier: "{{ PATIENT_AUTHORIZATION_CODE_VERIFIER }}",
  code_challenge: "{{ PATIENT_AUTHORIZATION_CODE_CHALLENGE }}",
  ORGANIZATION_TOP_LEVEL_PART_OF_ID: 0,
  ORGANIZATION_TOP_LEVEL_PART_OF_LABEL: "None (Top Level Organization)",
  ORGANIZATION_TYPES: {{ ORGANIZATION_TYPES }},
  DATA_SOURCE_TYPES: {{ DATA_SOURCE_TYPES }}
};


// RBAC permissions
window.ROLE_PERMISSIONS = {
  "super_user": [
    "data_source.manage",
    "organization.manage_for_practitioners",
    "patient.manage_for_organization",
    "study.manage_for_organization"
  ],
  "manager": [
    "organization.manage_for_practitioners",
    "patient.manage_for_organization",
    "study.manage_for_organization"
  ],
  "member": [
    "patient.manage_for_organization",
    "study.manage_for_organization"
  ],
  "viewer": []
};

{% endautoescape %}
