/**
 * OW Client - Open Wearables POC integration
 *
 * Flow:
 * 1. Parse invite link components from URL
 * 2. Exchange auth code for JHE access token
 * 3. Create OW user via JHE proxy (seamless, no login)
 * 4. Fetch available wearable providers from OW
 * 5. Display provider picker grid
 * 6. User selects provider → get OAuth URL → redirect to provider
 * 7. After OAuth, provider redirects back to /ow/complete
 */

// Module-level state
let _accessToken = null;

async function owProcessInviteLink() {
  const statusEl = document.getElementById("ow-status");
  const pickerEl = document.getElementById("ow-provider-picker");
  const gridEl = document.getElementById("ow-provider-grid");

  function setStatus(msg, isError) {
    if (statusEl) {
      statusEl.textContent = msg;
      statusEl.className = isError ? "text-danger" : "text-muted";
    }
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

    // Step 2: Exchange auth code for access token
    const tokens = await exchangeCodeForToken({
      host: invite.host,
      client_id: invite.client_id,
      code: invite.code,
      code_verifier: invite.code_verifier,
      redirect_uri: window.location.origin + "/auth/callback",
    });
    _accessToken = tokens.access_token;

    // Step 3: Create OW user (seamless, no login required)
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
      return;
    }

    // Step 4: Fetch available providers
    setStatus("Loading available devices...", false);
    const providersResp = await fetch("/api/v1/ow/providers", {
      headers: { Authorization: `Bearer ${_accessToken}` },
    });

    if (!providersResp.ok) {
      setStatus("Could not load available devices. Please try again.", true);
      return;
    }

    const providers = await providersResp.json();

    if (!providers || providers.length === 0) {
      setStatus("No wearable devices are currently available.", true);
      return;
    }

    // Step 5: Render provider picker
    statusEl.style.display = "none";
    pickerEl.style.display = "block";
    renderProviderGrid(gridEl, providers);

  } catch (err) {
    setStatus(`Something went wrong: ${err.message}`, true);
    console.error("OW Client error:", err);
  }
}

function renderProviderGrid(container, providers) {
  container.innerHTML = "";

  providers.forEach(function(provider) {
    if (!provider.is_enabled) return;

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

    if (provider.icon_url) {
      var iconWrapper = document.createElement("div");
      iconWrapper.className = "mb-3 p-3 bg-white rounded-3";
      iconWrapper.style.width = "80px";
      iconWrapper.style.height = "80px";
      iconWrapper.style.display = "flex";
      iconWrapper.style.alignItems = "center";
      iconWrapper.style.justifyContent = "center";

      var img = document.createElement("img");
      img.src = provider.icon_url;
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
