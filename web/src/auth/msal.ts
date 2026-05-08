import {
  PublicClientApplication,
  type Configuration,
  type AccountInfo,
  type RedirectRequest,
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
// `prompt: "select_account"` forces Microsoft to show the account picker every
// time, instead of silently re-using whatever account is cached in the
// browser's Microsoft session cookie. Without this, a stale account (e.g. a
// service account like `exclaimer@<tenant>` that the user once typed into the
// page) auto-fills the email field and the user gets a confusing "couldn't
// sign you in" error before they realise it's the wrong identity.
export const loginRequest: RedirectRequest = {
  scopes: ["openid", "profile", "email", apiScope].filter(Boolean) as string[],
  prompt: "select_account",
};

// Small in-process pub/sub for MSAL sign-in errors. main.tsx populates it from
// `handleRedirectPromise()` and `EventType.LOGIN_FAILURE`; AuthGate subscribes
// so we can surface the error inline above the "Sign in with Microsoft"
// button. Without this, a failed redirect just dumps the user back at the
// login screen with no explanation.
export interface SsoErrorDetails {
  code: string | null;
  message: string;
  correlationId: string | null;
  timestamp: number;
}

let currentSsoError: SsoErrorDetails | null = null;
let ssoErrorListeners: Array<(err: SsoErrorDetails | null) => void> = [];

export function getSsoError(): SsoErrorDetails | null {
  return currentSsoError;
}

export function setSsoError(err: SsoErrorDetails | null) {
  currentSsoError = err;
  for (const fn of ssoErrorListeners) fn(currentSsoError);
}

export function subscribeSsoError(
  fn: (err: SsoErrorDetails | null) => void,
): () => void {
  ssoErrorListeners.push(fn);
  return () => {
    ssoErrorListeners = ssoErrorListeners.filter((l) => l !== fn);
  };
}

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
