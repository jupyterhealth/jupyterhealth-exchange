/**
 * OW Client - Open Wearables POC integration
 *
 * Flow:
 * 1. Parse invite link components from URL
 * 2. Exchange auth code for JHE access token
 * 3. Fetch user profile + study consents from JHE
 * 4. Create OW user via JHE proxy (seamless, no login)
 * 5. Fetch available wearable providers from OW
 * 6. Render study/consent log panel + provider picker grid
 * 7. User selects provider → get OAuth URL → redirect to provider
 * 8. After OAuth, provider redirects back to /ow/complete
 */

// Module-level state
let _accessToken = null;

async function owProcessInviteLink() {
  const statusEl = document.getElementById("ow-status");
  const pickerEl = document.getElementById("ow-provider-picker");
  const gridEl = document.getElementById("ow-provider-grid");
  const logEl = document.getElementById("ow-log-panel");

  function setStatus(msg, isError) {
    if (statusEl) {
      statusEl.textContent = msg;
      statusEl.className = isError ? "text-danger" : "text-muted";
    }
  }

  function logLine(text, indent) {
    if (!logEl) return;
    var line = document.createElement("div");
    if (indent) line.style.paddingLeft = (indent * 16) + "px";
    line.textContent = text;
    logEl.querySelector(".log-body").appendChild(line);
  }

  function logHr() {
    if (!logEl) return;
    var hr = document.createElement("hr");
    hr.style.margin = "4px 0";
    hr.style.borderColor = "#444";
    logEl.querySelector(".log-body").appendChild(hr);
  }

  try {
    // Step 1: Parse invite link
    setStatus("Setting up your account...", false);
    const urlParams = new URLSearchParams(window.location.search);
    const inviteCode = urlParams.get("code");

    if (!inviteCode) {
      setStatus("Invalid invite link. Please check your link and try again.", true);
      return;
    }

    const invite = parseInvitationCode(inviteCode);

    // Show log panel
    if (logEl) logEl.style.display = "block";
    logLine("[" + new Date().toISOString() + "] Invite link parsed");
    logLine("Host: " + invite.host, 1);

    // Step 2: Exchange auth code for access token
    const tokens = await exchangeCodeForToken({
      host: invite.host,
      client_id: invite.client_id,
      code: invite.code,
      code_verifier: invite.code_verifier,
      redirect_uri: window.location.origin + "/auth/callback",
    });
    _accessToken = tokens.access_token;
    logLine("[" + new Date().toISOString() + "] Token exchange successful");
    logHr();

    // Step 3: Fetch user profile to get patient ID
    setStatus("Loading your studies...", false);
    logLine("[" + new Date().toISOString() + "] Fetching user profile...");

    const profileResp = await fetch("/api/v1/users/profile", {
      headers: { Authorization: "Bearer " + _accessToken },
    });

    var consentsData = null;
    if (profileResp.ok) {
      var profile = await profileResp.json();
      var patientId = profile.patient ? profile.patient.id : null;
      logLine("User ID: " + profile.id + " | Patient ID: " + (patientId || "N/A"), 1);

      if (patientId) {
        // Step 3b: Fetch study consents
        logLine("[" + new Date().toISOString() + "] Fetching study consents...");
        var consentsResp = await fetch("/api/v1/patients/" + patientId + "/consents", {
          headers: { Authorization: "Bearer " + _accessToken },
        });
        if (consentsResp.ok) {
          consentsData = await consentsResp.json();
          logLine("[" + new Date().toISOString() + "] Consents retrieved successfully");
        } else {
          logLine("[WARN] Could not fetch consents: HTTP " + consentsResp.status, 1);
        }
      }
    } else {
      logLine("[WARN] Could not fetch profile: HTTP " + profileResp.status, 1);
    }
    logHr();

    // Step 4: Create OW user (seamless, no login required)
    setStatus("Setting up wearable account...", false);
    const owUserResp = await fetch("/api/v1/ow/users", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${_accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!owUserResp.ok) {
      const errData = await owUserResp.json().catch(() => ({}));
      setStatus(`Account setup failed: ${errData.error || owUserResp.status}`, true);
      logLine("[ERROR] OW user creation failed: " + (errData.error || owUserResp.status));
      return;
    }

    var owUserData = await owUserResp.json();
    logLine("[" + new Date().toISOString() + "] OW user ready (created: " + (owUserData.created || false) + ")");
    logHr();

    // Step 5: Fetch available providers from OW
    setStatus("Loading available devices...", false);
    const providersResp = await fetch("/api/v1/ow/providers", {
      headers: { Authorization: `Bearer ${_accessToken}` },
    });

    if (!providersResp.ok) {
      setStatus("Could not load available devices. Please try again.", true);
      logLine("[ERROR] Could not fetch OW providers: HTTP " + providersResp.status);
      return;
    }

    const providers = await providersResp.json();
    logLine("[" + new Date().toISOString() + "] OW providers loaded: " + providers.length + " available");

    // Build OW provider name set for cross-reference
    var owProviderNames = {};
    providers.forEach(function(p) {
      owProviderNames[p.name.toLowerCase()] = p;
    });

    // Step 6: Render study/consent log
    if (consentsData) {
      logHr();
      logLine("===== STUDY ENROLLMENT & CONSENTS =====");

      var allStudies = (consentsData.studies_pending_consent || []).concat(consentsData.studies || []);
      if (allStudies.length === 0) {
        logLine("No studies found for this patient.", 1);
      }

      allStudies.forEach(function(study) {
        logHr();
        var orgName = study.organization ? study.organization.name : "Unknown Org";
        logLine("STUDY: " + study.name + "  [Org: " + orgName + "]");
        if (study.description) {
          logLine("Description: " + study.description, 1);
        }

        // Data sources (devices) for this study
        if (study.data_sources && study.data_sources.length > 0) {
          logLine("Data Sources / Devices:", 1);
          study.data_sources.forEach(function(ds) {
            var dsType = ds.type === "personal_device" ? "Personal Device" : (ds.type === "medical_device" ? "Medical Device" : ds.type);
            var owMatch = owProviderNames[ds.name.toLowerCase()];
            var owStatus = owMatch ? "YES (enabled in OW)" : "NOT FOUND in OW";
            logLine("- " + ds.name + " (" + dsType + ") — OW Integration: " + owStatus, 2);

            if (ds.supported_scopes && ds.supported_scopes.length > 0) {
              ds.supported_scopes.forEach(function(scope) {
                logLine("Supports: " + scope.text + " [" + scope.coding_code + "]", 3);
              });
            }
          });
        } else {
          logLine("Data Sources: None configured", 1);
        }

        // Scope consents
        var scopes = study.scope_consents || study.pending_scope_consents || [];
        if (scopes.length > 0) {
          var isPending = !!study.pending_scope_consents;
          logLine("Consent Scopes" + (isPending ? " (PENDING)" : " (RESPONDED)") + ":", 1);
          scopes.forEach(function(sc) {
            var status;
            if (sc.consented === true) {
              status = "CONSENTED";
              if (sc.consented_time) status += " at " + sc.consented_time;
            } else if (sc.consented === false) {
              status = "REJECTED";
            } else {
              status = "PENDING";
            }
            var scopeText = sc.code ? sc.code.text : "Unknown scope";
            var scopeCode = sc.code ? sc.code.coding_code : "";
            logLine("- " + scopeText + " [" + scopeCode + "]: " + status, 2);
          });
        }
      });
      logHr();
      logLine("===== END CONSENTS =====");
    }

    logHr();
    logLine("[" + new Date().toISOString() + "] Available OW Providers:");
    providers.forEach(function(p) {
      if (p.isEnabled) {
        logLine("- " + p.name + " (cloud API: " + p.hasCloudApi + ")", 1);
      }
    });

    if (!providers || providers.length === 0) {
      setStatus("No wearable devices are currently available.", true);
      return;
    }

    // Step 7: Render provider picker
    statusEl.style.display = "none";
    pickerEl.style.display = "block";
    renderProviderGrid(gridEl, providers);

  } catch (err) {
    setStatus(`Something went wrong: ${err.message}`, true);
    if (logEl) logLine("[ERROR] " + err.message);
    console.error("OW Client error:", err);
  }
}

function renderProviderGrid(container, providers) {
  container.innerHTML = "";

  providers.forEach(function(provider) {
    if (!provider.isEnabled) return;

    var col = document.createElement("div");
    col.className = "col-md-4 col-sm-6 mb-4";

    var card = document.createElement("div");
    card.className = "card h-100 text-center shadow-sm";
    card.style.cursor = "pointer";
    card.style.transition = "transform 0.2s, box-shadow 0.2s";
    card.onmouseenter = function() {
      card.style.transform = "translateY(-4px)";
      card.style.boxShadow = "0 8px 25px rgba(0,0,0,0.15)";
    };
    card.onmouseleave = function() {
      card.style.transform = "";
      card.style.boxShadow = "";
    };
    card.onclick = function() { handleProviderSelect(provider.provider, provider.name, card); };

    var body = document.createElement("div");
    body.className = "card-body d-flex flex-column align-items-center justify-content-center py-4";

    if (provider.iconUrl) {
      var iconWrapper = document.createElement("div");
      iconWrapper.className = "mb-3 p-3 bg-white rounded-3";
      iconWrapper.style.width = "80px";
      iconWrapper.style.height = "80px";
      iconWrapper.style.display = "flex";
      iconWrapper.style.alignItems = "center";
      iconWrapper.style.justifyContent = "center";

      var img = document.createElement("img");
      img.src = provider.iconUrl;
      img.alt = provider.name + " logo";
      img.style.maxWidth = "56px";
      img.style.maxHeight = "56px";
      img.style.objectFit = "contain";
      img.onerror = function() { iconWrapper.innerHTML = '<i class="bi bi-smartwatch fs-1 text-primary"></i>'; };

      iconWrapper.appendChild(img);
      body.appendChild(iconWrapper);
    }

    var name = document.createElement("h5");
    name.className = "card-title mb-2";
    name.textContent = provider.name;
    body.appendChild(name);

    var btn = document.createElement("span");
    btn.className = "text-primary fw-semibold";
    btn.innerHTML = 'Connect <i class="bi bi-chevron-right"></i>';
    body.appendChild(btn);

    card.appendChild(body);
    col.appendChild(card);
    container.appendChild(col);
  });
}

async function handleProviderSelect(providerId, providerName, cardEl) {
  // Disable all cards while loading
  var allCards = document.querySelectorAll("#ow-provider-grid .card");
  allCards.forEach(function(c) {
    c.style.pointerEvents = "none";
    c.style.opacity = "0.5";
  });

  // Show loading state on selected card
  cardEl.style.opacity = "1";
  var btn = cardEl.querySelector(".text-primary");
  if (btn) {
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Connecting...';
  }

  try {
    var redirectUri = window.location.origin + "/ow/complete";
    var authResp = await fetch(
      "/api/v1/ow/oauth/" + encodeURIComponent(providerId) + "/authorize?redirect_uri=" + encodeURIComponent(redirectUri),
      {
        headers: { Authorization: "Bearer " + _accessToken },
      },
    );

    if (!authResp.ok) {
      var errData = await authResp.json().catch(function() { return {}; });
      throw new Error(errData.error || errData.detail || ("HTTP " + authResp.status));
    }

    var authData = await authResp.json();
    var authUrl = authData.authorization_url || authData.authorizationUrl;

    if (!authUrl) {
      throw new Error("No authorization URL returned");
    }

    // Redirect to provider OAuth page
    window.location.href = authUrl;

  } catch (err) {
    // Re-enable cards on error
    allCards.forEach(function(c) {
      c.style.pointerEvents = "";
      c.style.opacity = "";
    });
    if (btn) {
      btn.innerHTML = 'Connect <i class="bi bi-chevron-right"></i>';
    }
    alert("Could not connect to " + providerName + ": " + err.message);
    console.error("Provider authorize error:", err);
  }
}

/**
 * Called on the /ow/complete page after OAuth flow finishes.
 */
function owProcessComplete() {
  var statusEl = document.getElementById("ow-complete-status");
  if (statusEl) {
    statusEl.textContent = "Your device has been connected and will start syncing data shortly.";
  }
}
