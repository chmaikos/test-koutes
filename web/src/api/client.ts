import axios, { type InternalAxiosRequestConfig } from "axios";
import {
  acquireApiToken,
  clearCachedAccounts,
  msalInstance,
  setSsoError,
  ssoAvailable,
} from "@/auth/msal";
import { clearLocalSession, getLocalToken } from "@/auth/local";

const baseURL = (import.meta.env.VITE_API_BASE_URL as string) || "/api";

export const api = axios.create({ baseURL });

api.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    config.headers = config.headers ?? {};
    const localToken = getLocalToken();
    if (localToken) {
      config.headers.Authorization = `Bearer ${localToken}`;
      return config;
    }
    if (!ssoAvailable()) {
      // No local token and SSO is off — let the request go out unauthenticated;
      // the API will return 401 and the AuthGate will be visible already.
      return config;
    }
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length > 0) {
      try {
        const token = await acquireApiToken(accounts[0]);
        config.headers.Authorization = `Bearer ${token}`;
      } catch (err) {
        console.warn("[api] failed to acquire token", err);
      }
    }
    return config;
  },
);

// Guard so a burst of failing API calls (every page makes several `useQuery`
// requests on mount) doesn't all race to clear the MSAL cache or trigger
// concurrent re-auth flows. Resets when a request finally succeeds.
let unauthorizedRecoveryInFlight: Promise<void> | null = null;

api.interceptors.response.use(
  (resp) => {
    unauthorizedRecoveryInFlight = null;
    return resp;
  },
  (error) => {
    const status = error?.response?.status;
    if (status === 401) {
      const hadLocal = getLocalToken() !== null;
      if (hadLocal) {
        clearLocalSession();
      } else if (ssoAvailable()) {
        // DON'T auto-call `msalInstance.loginRedirect()` here. That used to
        // sit in this slot and was the cause of the visible "SPA refreshes
        // repeatedly through Microsoft's loading-your-account page until it
        // hits the error" loop:
        //   1. Cached MSAL account is stale -> `useIsAuthenticated()` is true
        //      -> AppShell renders -> `useQuery` fires API calls.
        //   2. API returns 401.
        //   3. The old interceptor called `loginRedirect()` with no args
        //      (no scopes, no prompt, no domain_hint, no cache wipe), which
        //      silently appended `login_hint`/`X-AnchorMailbox` for the
        //      stale account.
        //   4. Microsoft couldn't satisfy that, returned the user, MSAL
        //      still had the same stale account, AppShell re-rendered, more
        //      API calls fired - goto step 2.
        // The fix is to clear MSAL's cache on the first 401 in a recovery
        // window. That flips `useIsAuthenticated()` to false, AuthGate
        // re-renders the sign-in form, and the user clicks Sign in cleanly
        // (which goes through the proper loginRequest with prompt =
        // select_account and a fresh cache).
        if (!unauthorizedRecoveryInFlight) {
          unauthorizedRecoveryInFlight = (async () => {
            try {
              await clearCachedAccounts();
              setSsoError({
                code: "STALE_SESSION",
                message:
                  "Your previous Microsoft session expired or was rejected by the API. Please sign in again.",
                correlationId: null,
                timestamp: Date.now(),
              });
            } catch (err) {
              console.warn("[api] failed to recover from 401", err);
            }
          })();
        }
      }
    }
    return Promise.reject(error);
  },
);

// Keep a reference to the in-flight token so that callers awaiting the
// interceptor recovery can chain off it if they need to.
export function getUnauthorizedRecovery(): Promise<void> | null {
  return unauthorizedRecoveryInFlight;
}

export function buildQueryString(params: Record<string, unknown>): string {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    out[k] = String(v);
  }
  const q = new URLSearchParams(out).toString();
  return q ? `?${q}` : "";
}
