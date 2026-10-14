import { afterEach, expect, it, vi } from "vitest";
import { purgeLegacyApiCaches } from "./cache";

afterEach(() => vi.unstubAllGlobals());

it("purges every legacy API cache while preserving app shell assets", async () => {
  const remove = vi.fn().mockResolvedValue(true);
  vi.stubGlobal("caches", { keys: async () => ["warehouse-api-get", "warehouse-api-get-v2", "workbox-precache-v2"], delete: remove });
  await purgeLegacyApiCaches();
  expect(remove.mock.calls).toEqual([["warehouse-api-get"], ["warehouse-api-get-v2"]]);
});

it("allows sign-in when CacheStorage is unavailable", async () => {
  vi.stubGlobal("caches", { keys: vi.fn().mockRejectedValue(new Error("access denied")) });
  await expect(purgeLegacyApiCaches()).resolves.toBeUndefined();
});
