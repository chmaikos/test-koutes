import { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from "axios";
import { expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ token: "account-a", clear: vi.fn() }));
vi.mock("@/auth/local", () => ({ getLocalToken: () => auth.token, clearLocalSession: auth.clear }));
vi.mock("@/auth/msal", () => ({
  acquireApiToken: vi.fn(), clearCachedAccounts: vi.fn(), setSsoError: vi.fn(), ssoAvailable: () => false,
  msalInstance: { getActiveAccount: () => null, getAllAccounts: () => [] },
}));
import { api } from "./client";

it("a delayed 401 from the previous account cannot log out the replacement account", async () => {
  let rejectResponse: (reason: unknown) => void = () => {};
  let oldConfig: InternalAxiosRequestConfig | undefined;
  api.defaults.adapter = async (config) => {
    oldConfig = config;
    return new Promise((_resolve, reject) => { rejectResponse = reject; });
  };
  const request = api.get("/auth/me");
  const result = expect(request).rejects.toBeInstanceOf(AxiosError);
  await vi.waitFor(() => expect(oldConfig).toBeDefined());
  auth.token = "account-b";
  rejectResponse(new AxiosError("unauthorized", "ERR_BAD_REQUEST", oldConfig, undefined, {
    status: 401, statusText: "Unauthorized", data: {}, headers: new AxiosHeaders(), config: oldConfig!,
  }));
  await result;
  expect(auth.clear).not.toHaveBeenCalled();
});
