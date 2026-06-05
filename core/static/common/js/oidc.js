var userManager = null;

// Shared OAuth/OIDC error-relay helpers (see GH #192). When the provider returns
// an error (eg. an invalid OIDC_RSA_PRIVATE_KEY) these surface it to the user and
// flag the failure so the SPA does not silently retry sign-in indefinitely.
window.AUTH_ERROR_STORAGE_KEY = "jheAuthError";

window.describeOidcError = (error) => {
  if (!error) return "Sign-in failed. Please try again.";
  if (typeof error === "string") return error;
  return (
    error.error_description ||
    error.error ||
    error.message ||
    "Sign-in failed. Please try again."
  );
};

window.setAuthError = (message) => {
  try {
    sessionStorage.setItem(window.AUTH_ERROR_STORAGE_KEY, message);
  } catch (e) {
    console.error(e);
  }
};

window.getAuthError = () => {
  try {
    return sessionStorage.getItem(window.AUTH_ERROR_STORAGE_KEY);
  } catch (e) {
    console.error(e);
    return null;
  }
};

window.clearAuthError = () => {
  try {
    sessionStorage.removeItem(window.AUTH_ERROR_STORAGE_KEY);
  } catch (e) {
    console.error(e);
  }
};

// Replace the "logging in" spinner with the error message. Templates that use
// this provide #authLoading (spinner) and #authError / #authErrorMessage blocks.
window.showAuthError = (message) => {
  const loading = document.getElementById("authLoading");
  if (loading) loading.style.display = "none";
  const errorMessage = document.getElementById("authErrorMessage");
  if (errorMessage) errorMessage.textContent = message;
  const errorBox = document.getElementById("authError");
  if (errorBox) errorBox.style.display = "block";
};

window.initOidc = () => {
  const Log = window.oidc.Log;
  const UserManager = window.oidc.UserManager;
  const WebStorageStateStore = window.oidc.WebStorageStateStore;

  Log.setLogger(console);
  Log.setLevel(Log.INFO);
  function debugAuthOut(...args) {
    args.forEach((msg) => {
      if (msg instanceof Error) msg = "Error: " + msg.message;
      else if (typeof msg !== "string") msg = JSON.stringify(msg, null, 2);
      console.log(msg);
    });
  }

  // Extra params can be configured, eg:
  // window.OIDCSettings.extraQueryParams['hello'] = 'world'

  window.OIDCSettings.userStore = new WebStorageStateStore({
    store: window.localStorage,
  });
  userManager = new UserManager(window.OIDCSettings);

  userManager.events.addUserLoaded(function (user) {
    userManager.getUser().then(
      function () {
        console.log(
          "window.initOidc - userManager.events.addUserLoaded"
        );
      },
      () => {}
    );
  });

  userManager.events.addUserUnloaded(function (e) {
    console.log("window.initOidc - userManager.events.addUserUnloaded");
  });

  /**
   * Testing Functions
   */

  const clearState = () => {
    userManager
      .clearStaleState()
      .then(() => {
        debugAuthOut("userManager: clearStateState success");
      })
      .catch((err) => {
        console.error(err);
        debugAuthOut(err);
      });
  };

  const getUser = () => {
    userManager
      .getUser()
      .then((user) => {
        debugAuthOut("userManager: got user", user);
      })
      .catch((err) => {
        console.error(err);
        debugAuthOut(err);
      });
  };

  const removeUser = () => {
    userManager
      .removeUser()
      .then(() => {
        debugAuthOut("userManager: user removed");
      })
      .catch((err) => {
        console.error(err);
        debugAuthOut(err);
      });
  };

  const redirectSignin = () => {
    removeUser();
    clearState();
    userManager
      .signinRedirect()
      .then((user) => {
        debugAuthOut("userManager: signed in", user);
      })
      .catch((err) => {
        console.error(err);
        debugAuthOut(err);
      });
  };

  const popupSignin = () => {
    removeUser();
    clearState();
    userManager
      .signinPopup()
      .then((user) => {
        debugAuthOut("userManager: signed in", user);
      })
      .catch((err) => {
        console.error(err);
        debugAuthOut(err);
      });
  };

  // Automatically start the iFrame login on page load iframeSignin()
  const iframeSignin = () => {
    removeUser();
    clearState();
    userManager
      .signinSilent()
      .then((user) => {
        debugAuthOut("userManager: signed in", user);
      })
      .catch((err) => {
        console.error(err);
        debugAuthOut(err);
      });
  };

};
