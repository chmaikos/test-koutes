import { purgeLegacyApiCaches } from "@/auth/cache";
import axios from "axios";
import type { LocalLoginResponse, User } from "@/api/types";

const baseURL = (import.meta.env.VITE_API_BASE_URL as string) || "/api";
const LS_TOKEN = "warehouse.localToken";
const LS_USER = "warehouse.localUser";

let listeners: Array<() => void> = [];
let sessionEpoch = 0;

function notify() {
  sessionEpoch += 1;
  for (const fn of listeners) fn();
}

export function subscribeLocalAuth(fn: () => void): () => void {
  listeners.push(fn);
  return () => {
    listeners = listeners.filter((l) => l !== fn);
  };
}

export function getLocalToken(): string | null {
  return localStorage.getItem(LS_TOKEN);
}

export function getCachedLocalUser(): User | null {
  const raw = localStorage.getItem(LS_USER);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export function clearLocalSession() {
  localStorage.removeItem(LS_TOKEN);
  localStorage.removeItem(LS_USER);
  void purgeLegacyApiCaches();
  notify();
}

export async function localLogin(
  username: string,
  password: string,
): Promise<LocalLoginResponse> {
  const attempt = ++sessionEpoch;
  const originalToken = getLocalToken();
  const resp = await axios.post<LocalLoginResponse>(
    `${baseURL}/auth/login`,
    { username, password },
  );
  await purgeLegacyApiCaches();
  if (attempt !== sessionEpoch || getLocalToken() !== originalToken) {
    throw new Error("Sign-in was superseded by another account change.");
  }
  localStorage.setItem(LS_TOKEN, resp.data.access_token);
  localStorage.setItem(LS_USER, JSON.stringify(resp.data.user));
  notify();
  return resp.data;
}

export async function changeLocalCredentials(input: {
  current_password: string;
  new_password: string;
  new_username?: string;
}): Promise<User> {
  const token = getLocalToken();
  if (!token) throw new Error("not signed in");
  const originalEpoch = sessionEpoch;
  const resp = await axios.post<User>(
    `${baseURL}/auth/change-credentials`,
    input,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (sessionEpoch !== originalEpoch || getLocalToken() !== token) {
    throw new Error("Your session changed while credentials were being updated.");
  }
  const replacementToken = resp.headers["x-local-access-token"];
  if (!replacementToken) throw new Error("credential change did not return a replacement session");
  localStorage.setItem(LS_TOKEN, replacementToken);
  localStorage.setItem(LS_USER, JSON.stringify(resp.data));
  notify();
  return resp.data;
}

// Keep other tabs on a shared device aligned with logout/account changes.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === LS_TOKEN || event.key === null) {
      void purgeLegacyApiCaches();
      notify();
    }
  });
}

// FastAPI validation errors carry a list of structured details; React cannot
// render those objects directly, so expose only readable messages.
export function localAuthError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => typeof item?.msg === "string" ? item.msg : null).filter(Boolean);
    if (messages.length) return messages.join(" ");
  }
  return error instanceof Error ? error.message : fallback;
}
