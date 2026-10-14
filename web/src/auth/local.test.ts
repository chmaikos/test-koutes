import { beforeEach, expect, it, vi } from "vitest";
import axios from "axios";
import { changeLocalCredentials, clearLocalSession, getLocalToken, localAuthError, localLogin, subscribeLocalAuth } from "./local";

vi.mock("axios", () => ({ default: { post: vi.fn() } }));
vi.mock("./cache", () => ({ purgeLegacyApiCaches: vi.fn().mockResolvedValue(undefined) }));

beforeEach(() => {
  const values = new Map([["warehouse.localToken", "old-token"]]);
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  });
});

it("installs rotated credentials before notifying authenticated consumers", async () => {
  const user = { id: 1, username: "rotated" };
  vi.mocked(axios.post).mockResolvedValueOnce({ data: user, headers: { "x-local-access-token": "replacement" } });
  const observed: Array<string | null> = [];
  const unsubscribe = subscribeLocalAuth(() => observed.push(getLocalToken()));
  await expect(changeLocalCredentials({ current_password: "current-password", new_password: "updated-password" })).resolves.toEqual(user);
  expect(observed).toEqual(["replacement"]);
  expect(axios.post).toHaveBeenCalledWith("/api/auth/change-credentials", expect.any(Object), { headers: { Authorization: "Bearer old-token" } });
  unsubscribe();
});

it("discards a credential rotation response when logout happened in flight", async () => {
  let respond: (value: unknown) => void = () => {};
  vi.mocked(axios.post).mockImplementationOnce(() => new Promise((resolve) => { respond = resolve; }));
  const pending = changeLocalCredentials({ current_password: "current-password", new_password: "updated-password" });
  clearLocalSession();
  respond({ data: { id: 1 }, headers: { "x-local-access-token": "late-token" } });
  await expect(pending).rejects.toThrow("session changed");
  expect(getLocalToken()).toBeNull();
});

it("discards an old login response after another login replaces it", async () => {
  clearLocalSession();
  let respond: (value: unknown) => void = () => {};
  vi.mocked(axios.post)
    .mockImplementationOnce(() => new Promise((resolve) => { respond = resolve; }))
    .mockResolvedValueOnce({ data: { access_token: "new-account", user: { id: 2 } } });
  const oldLogin = localLogin("first", "password");
  await localLogin("second", "password");
  respond({ data: { access_token: "late-account", user: { id: 1 } } });
  await expect(oldLogin).rejects.toThrow("superseded");
  expect(getLocalToken()).toBe("new-account");
});

it("normalizes validation error arrays into readable auth errors", () => {
  expect(localAuthError({ response: { data: { detail: [{ loc: ["password"], msg: "Password too long" }] } } }, "Failed"))
    .toBe("Password too long");
  expect(localAuthError({ response: { data: { detail: [{ unexpected: true }] } } }, "Failed"))
    .toBe("Failed");
});
