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

// Parse invitation link code parameter into its components: format is host_token.
function parseInvitationCode(code) {
  var parts = code.split("_");
  if (parts.length !== 2) {
    return null;
  }
  return {
    host: decodeURIComponent(parts[0]),
    token: parts[1],
  };
}

// Exposed for unit tests; browser runs load this as a plain <script> and ignore it.
if (typeof window !== "undefined") {
  window.parseInvitationCode = parseInvitationCode;
}
