/**
 * OW Client - Open Wearables POC integration
 *
 * Flow (steps 1-8 from launch.html):
 * 1. Parse invite link components from URL
 * 2. Exchange auth code for JHE access token
 * 3. Fetch and auto-accept consents (POC)
 * 4-5. Create OW user via JHE proxy endpoint
 * 6-7. Get Oura OAuth URL via JHE proxy endpoint
 * 8. Redirect to Oura OAuth
 */

async function owProcessInviteLink() {
  const out = document.getElementById("out");

  try {
    // Step 1: Parse invite link from URL query params
    out.textContent = "Step 1: Parsing invite link...";
    const urlParams = new URLSearchParams(window.location.search);
    const inviteCode = urlParams.get("code");

    if (!inviteCode) {
      out.textContent += "\nError: No invite code found. URL should include ?code=host~client_id~code~code_verifier";
      return;
    }

    const invite = parseInvitationCode(inviteCode);
    out.textContent += `\n  Host: ${invite.host}`;
    out.textContent += `\n  Client ID: ${invite.client_id}`;
    out.textContent += `\n  Auth Code: ${invite.code.substring(0, 8)}...`;
    out.textContent += `\n  Code Verifier: ${invite.code_verifier.substring(0, 8)}...`;

    // Step 2: Exchange auth code for access token
    out.textContent += "\n\nStep 2: Exchanging auth code for access token...";
    const tokens = await exchangeCodeForToken({
      host: invite.host,
      client_id: invite.client_id,
      code: invite.code,
      code_verifier: invite.code_verifier,
      redirect_uri: window.location.origin + "/ow/",
    });
    const accessToken = tokens.access_token;
    out.textContent += "\n  Access token obtained successfully.";

    // Step 3: Fetch and auto-accept consents (POC - assume yes to all)
    out.textContent += "\n\nStep 3: Fetching consents...";
    const consentsResp = await fetch("/api/v1/studies", {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (consentsResp.ok) {
      out.textContent += "\n  Consents fetched (auto-accepted for POC).";
    } else {
      out.textContent += `\n  Warning: Could not fetch consents (${consentsResp.status}). Continuing...`;
    }

    // Steps 4-5: Create OW user via JHE proxy
    out.textContent += "\n\nStep 4-5: Creating user in Open Wearables...";
    const owUserResp = await fetch("/api/v1/ow/users", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!owUserResp.ok) {
      const errData = await owUserResp.json().catch(() => ({}));
      out.textContent += `\n  Error creating OW user: ${errData.error || owUserResp.status}`;
      return;
    }

    const owUserData = await owUserResp.json();
    out.textContent += `\n  OW User ID: ${owUserData.ow_user_id}`;
    out.textContent += `\n  Created: ${owUserData.created}`;

    // Steps 6-7: Get Oura OAuth authorization URL
    out.textContent += "\n\nStep 6-7: Getting Oura authorization URL...";
    const redirectUri = window.location.origin + "/ow/complete";
    const ouraResp = await fetch(
      `/api/v1/ow/oauth/oura/authorize?redirect_uri=${encodeURIComponent(redirectUri)}`,
      {
        headers: { Authorization: `Bearer ${accessToken}` },
      },
    );

    if (!ouraResp.ok) {
      const errData = await ouraResp.json().catch(() => ({}));
      out.textContent += `\n  Error getting Oura auth URL: ${errData.error || ouraResp.status}`;
      return;
    }

    const ouraData = await ouraResp.json();
    out.textContent += `\n  Authorization URL: ${ouraData.authorization_url}`;

    // Step 8: Redirect to Oura OAuth
    out.textContent += "\n\nStep 8: Redirecting to Oura for authorization...";
    setTimeout(() => {
      window.location.href = ouraData.authorization_url;
    }, 2000);
  } catch (err) {
    out.textContent += `\n\nError: ${err.message}`;
    console.error("OW Client error:", err);
  }
}
