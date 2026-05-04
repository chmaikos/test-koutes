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
