import axios from "axios";
import type { LocalLoginResponse, User } from "@/api/types";

const baseURL = (import.meta.env.VITE_API_BASE_URL as string) || "/api";
const LS_TOKEN = "warehouse.localToken";
const LS_USER = "warehouse.localUser";

let listeners: Array<() => void> = [];

function notify() {
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
  notify();
}

export async function localLogin(
  username: string,
  password: string,
): Promise<LocalLoginResponse> {
  const resp = await axios.post<LocalLoginResponse>(
    `${baseURL}/auth/login`,
    { username, password },
  );
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
  const resp = await axios.post<User>(
    `${baseURL}/auth/change-credentials`,
    input,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  localStorage.setItem(LS_USER, JSON.stringify(resp.data));
  notify();
  return resp.data;
}
