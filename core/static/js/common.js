function validatePassword(str) {
  return /^(?=.*?[A-Z])(?=.*?[a-z])(?=.*?[0-9])(?=.*?[#?!@$%^&*-]).{8,}$/.test(str);
}

function validateEmail(str){
  return /(?:[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*|"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*")@(?:(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?|\[(?:(?:(2(5[0-5]|[0-4][0-9])|1[0-9][0-9]|[1-9]?[0-9]))\.){3}(?:(2(5[0-5]|[0-4][0-9])|1[0-9][0-9]|[1-9]?[0-9])|[a-z0-9-]*[a-z0-9]:(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21-\x5a\x53-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])+)\])/.test(str);
}

function isShallowEq(obj1, obj2) {
  for (const [key, value] of Object.entries(obj1)) {
    if (obj2[key] !== value) return false;
  }
  for (const [key, value] of Object.entries(obj2)) {
    if (obj1[key] !== value) return false;
  }
  return true;
}

function buildSelectOptions(source, selectedId, exclude=[]) {
  const options = [];
  for (const [id, label] of Object.entries(source)) {
    if(exclude.indexOf(id)==-1){
      options.push({
        id: id,
        label: label,
        selected: id === selectedId,
      });
    }
  }
  return options;
}

function base64UrlEncode(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }

  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

/**
 * Parse an invitation code string into its components.
 * Format: host~client_id~code~code_verifier
 */
function parseInvitationCode(invitationCode) {
  const parts = invitationCode.split("~");
  if (parts.length !== 4) {
    throw new Error("Invalid invitation code format. Expected: host~client_id~code~code_verifier");
  }
  return {
    host: parts[0],
    client_id: parts[1],
    code: parts[2],
    code_verifier: parts[3],
  };
}

/**
 * Exchange an authorization code for an access token via the OIDC token endpoint.
 */
async function exchangeCodeForToken({ host, client_id, code, code_verifier, redirect_uri }) {
  // Use window.location.origin for token endpoint (host from invite code is
  // just hostname:port without protocol, and we're always same-origin).
  const tokenEndpoint = `${window.location.origin}/o/token/`;
  const payload = {
    code,
    grant_type: "authorization_code",
    redirect_uri: redirect_uri || `${window.location.origin}/auth/callback/`,
    client_id,
    code_verifier,
  };

  const formData = new URLSearchParams(payload).toString();
  const response = await fetch(tokenEndpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "Cache-Control": "no-cache",
    },
    body: formData,
  });

  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(`Token exchange failed (${response.status}): ${errorBody}`);
  }

  return response.json();
}
