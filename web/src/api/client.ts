import axios, { type InternalAxiosRequestConfig } from "axios";
import { acquireApiToken, msalInstance } from "@/auth/msal";
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

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const status = error?.response?.status;
    if (status === 401) {
      const hadLocal = getLocalToken() !== null;
      if (hadLocal) {
        clearLocalSession();
      } else {
        msalInstance.loginRedirect().catch(() => {});
      }
    }
    return Promise.reject(error);
  },
);

export function buildQueryString(params: Record<string, unknown>): string {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    out[k] = String(v);
  }
  const q = new URLSearchParams(out).toString();
  return q ? `?${q}` : "";
}
