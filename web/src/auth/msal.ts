import {
  PublicClientApplication,
  type Configuration,
  type AccountInfo,
  type SilentRequest,
} from "@azure/msal-browser";

const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID || "common";
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID;
const apiScope = import.meta.env.VITE_API_SCOPE;

if (!clientId) {
  console.warn(
    "[auth] VITE_ENTRA_CLIENT_ID is not set; the Sign in button will fail."
  );
}

// MSAL.js needs window.crypto.subtle for PKCE code-verifier generation, which
// browsers only expose in a secure context (HTTPS, or http on localhost /
// 127.0.0.1). When you hit the SPA over plain HTTP via a LAN/WAN IP, MSAL
// throws BrowserAuthError "crypto_nonexistent" the moment we touch it. We
// detect that up-front so the rest of the app can fall back to the local-admin
// login path instead of crashing on a blank page.
const supportsWebCrypto =
  typeof window !== "undefined" &&
  typeof window.crypto !== "undefined" &&
  typeof window.crypto.subtle !== "undefined" &&
  // window.isSecureContext is true for HTTPS and for http://localhost.
  (typeof window.isSecureContext === "undefined" || window.isSecureContext);

let ssoAvailableFlag = supportsWebCrypto && !!clientId;

export const ssoAvailable = (): boolean => ssoAvailableFlag;

export function disableSso(reason?: string) {
  ssoAvailableFlag = false;
  if (reason) {
    console.warn(`[auth] Microsoft SSO disabled: ${reason}`);
  }
}

if (!supportsWebCrypto) {
  console.warn(
    "[auth] Microsoft SSO is unavailable in this context. The browser only " +
      "exposes window.crypto.subtle on HTTPS or http://localhost. The local " +
      "admin login still works.",
  );
}

const msalConfig: Configuration = {
  auth: {
    clientId: clientId || "00000000-0000-0000-0000-000000000000",
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin,
    postLogoutRedirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "localStorage",
    storeAuthStateInCookie: false,
  },
};

export const msalInstance = new PublicClientApplication(msalConfig);
export const loginRequest = {
  scopes: ["openid", "profile", "email", apiScope].filter(Boolean) as string[],
};

export async function acquireApiToken(account: AccountInfo): Promise<string> {
  if (!ssoAvailableFlag) {
    throw new Error("Microsoft SSO is not available in this context");
  }
  const request: SilentRequest = {
    scopes: [apiScope],
    account,
  };
  try {
    const result = await msalInstance.acquireTokenSilent(request);
    return result.accessToken;
  } catch {
    const result = await msalInstance.acquireTokenPopup(request);
    return result.accessToken;
  }
}

export const apiScopeValue = apiScope as string;
