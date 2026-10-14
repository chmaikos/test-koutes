import { expect, test } from "@playwright/test";

test.use({ serviceWorkers: "allow" });

test("production worker purges old account caches and never serves offline API identity", async ({ page, context }) => {
  // A static document does not run app startup cleanup or auto-registration.
  await page.goto("/favicon.svg");
  await page.evaluate(async () => {
    // Seed the cache before the first actual worker activates.
    const cache = await caches.open("warehouse-api-get-v2");
    await cache.put("/api/auth/me", new Response(JSON.stringify({ display_name: "PREVIOUS ACCOUNT" }), { headers: { "Content-Type": "application/json" } }));
    const registration = await navigator.serviceWorker.register("/sw.js");
    const worker = registration.installing ?? registration.waiting ?? registration.active;
    if (worker && worker.state !== "activated") {
      await new Promise<void>((resolve) => worker.addEventListener("statechange", () => {
        if (worker.state === "activated") resolve();
      }));
    }
  });
  await expect.poll(() => page.evaluate(() => caches.keys())).not.toContain("warehouse-api-get-v2");
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
  // Even if a legacy cache exists after activation, API routing must ignore it.
  await page.evaluate(async () => {
    const cache = await caches.open("warehouse-api-get-v2");
    await cache.put("/api/auth/me", new Response(JSON.stringify({ display_name: "PREVIOUS ACCOUNT" }), { headers: { "Content-Type": "application/json" } }));
  });
  await context.setOffline(true);
  const result = await page.evaluate(async () => {
    try {
      const response = await fetch("/api/auth/me");
      return { responded: true, body: await response.text() };
    } catch {
      return { responded: false, body: "" };
    }
  });
  expect(result.responded).toBe(false);
  expect(result.body).not.toContain("PREVIOUS ACCOUNT");
  await context.setOffline(false);
});
