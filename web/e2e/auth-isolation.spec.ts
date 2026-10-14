import { expect, test, type BrowserContext, type Page, type Route } from "@playwright/test";
import type { Box, User } from "../src/api/types";

function user(account: "A" | "B", mustChange = false): User {
  return {
    id: account === "A" ? 1 : 2, username: `account-${account.toLowerCase()}`,
    email: `${account.toLowerCase()}@example.test`, display_name: `Account ${account}`,
    role: "admin", role_override: false, entra_oid: null, is_active: true,
    is_local: true, must_change_credentials: mustChange, warehouse_ids: [],
    email_alerts_enabled: false, email_requests_enabled: false, last_login_at: null,
  };
}

function box(account: "A" | "B"): Box {
  return {
    id: account === "A" ? 1 : 2, barcode: "WBX-0000000001", box_number: "001",
    lot: `${account}-PRIVATE-INVENTORY`, lot_id: 1, pallet_id: null, pallet_number: null,
    contents: null, file_count: 0, active_file_count: 0, archived_file_count: 0, files: [],
    current_warehouse_id: 1, status: "received", received_at: null, processing_completed_at: null,
    returned_at: null, archived_at: null, archived_by_user_id: null, archive_reason: null,
    created_at: "2026-10-14T08:00:00Z", updated_at: "2026-10-14T08:00:00Z", receipt_request_id: null,
  };
}

type Override = (route: Route, account: "A" | "B") => Promise<boolean>;
async function mockAPI(context: BrowserContext, override?: Override) {
  await context.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const account = request.headers().authorization === "Bearer token-B" ? "B" : "A";
    if (override && await override(route, account)) return;
    const json = (value: unknown) => route.fulfill({ json: value });
    if (path === "/api/auth/login") {
      const loginAccount = request.postDataJSON().username === "account-b" ? "B" : "A";
      return json({ access_token: `token-${loginAccount}`, token_type: "bearer", expires_in_minutes: 480, must_change_credentials: false, user: user(loginAccount) });
    }
    if (path === "/api/auth/me") return json(user(account));
    if (path === "/api/dashboard/summary") return json({ warehouses: [], total_available_boxes: 0, total_unavailable_boxes: 0, total_completed_today: 0, total_open_alerts: 0 });
    if (path === "/api/warehouses") return json([]);
    if (path === "/api/boxes") return json({ items: [box(account)], total: 1, page: 1, page_size: 25 });
    if (path === "/api/notifications") return json({ items: [], total: 0, unread: 0, page: 1, page_size: 15 });
    if (path === "/api/stream") return route.fulfill({ contentType: "text/event-stream", body: "event: ready\ndata: {}\n\n" });
    return route.fulfill({ status: 404, json: { detail: "Unmocked API endpoint" } });
  });
}

async function login(page: Page, account: "A" | "B") {
  const localTab = page.getByRole("button", { name: "Local admin", exact: true });
  if (await localTab.isVisible()) await localTab.click();
  await page.getByLabel("Username", { exact: true }).fill(`account-${account.toLowerCase()}`);
  await page.getByLabel("Password", { exact: true }).fill("valid-password-123");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText(`Account ${account}`, { exact: true }).first()).toBeVisible();
}

function deferred() {
  let release: () => void = () => {};
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

test("shared-device sign-out/login starts with fresh identity and inventory", async ({ page, context }) => {
  const secondInventory = deferred();
  let requestedSecondInventory = false;
  await mockAPI(context, async (route, account) => {
    if (new URL(route.request().url()).pathname === "/api/boxes" && account === "B") {
      requestedSecondInventory = true;
      await secondInventory.promise;
      await route.fulfill({ json: { items: [box("B")], total: 1, page: 1, page_size: 25 } });
      return true;
    }
    return false;
  });
  await page.goto("/");
  await login(page, "A");
  await page.getByRole("link", { name: "Boxes", exact: true }).click();
  await expect(page.getByText("A-PRIVATE-INVENTORY", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await login(page, "B");
  await expect(page.getByText("Account A", { exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "Boxes", exact: true }).click();
  await expect.poll(() => requestedSecondInventory).toBe(true);
  await expect(page.getByText("A-PRIVATE-INVENTORY", { exact: true })).toHaveCount(0);
  secondInventory.release();
  await expect(page.getByText("B-PRIVATE-INVENTORY", { exact: true }).first()).toBeVisible();
});

test("late 401 from the previous account cannot sign out its replacement", async ({ page, context }) => {
  const firstNotifications = deferred();
  let pendingOldRequest = false;
  await mockAPI(context, async (route, account) => {
    if (new URL(route.request().url()).pathname === "/api/notifications" && account === "A") {
      pendingOldRequest = true;
      await firstNotifications.promise;
      await route.fulfill({ status: 401, json: { detail: "Expired old account" } });
      return true;
    }
    return false;
  });
  await page.goto("/");
  await login(page, "A");
  await expect.poll(() => pendingOldRequest).toBe(true);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await login(page, "B");
  const rejection = page.waitForResponse((response) => response.url().includes("/api/notifications") && response.status() === 401);
  firstNotifications.release();
  await rejection;
  await page.getByRole("link", { name: "Boxes", exact: true }).click();
  await expect(page.getByText("B-PRIVATE-INVENTORY", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Account B", { exact: true }).first()).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("warehouse.localToken"))).toBe("token-B");
});

test("another tab's account switch immediately resets rendered identity and inventory", async ({ page, context }) => {
  await mockAPI(context);
  await page.goto("/");
  await login(page, "A");
  await page.getByRole("link", { name: "Boxes", exact: true }).click();
  await expect(page.getByText("A-PRIVATE-INVENTORY", { exact: true }).first()).toBeVisible();
  const secondTab = await context.newPage();
  await secondTab.goto("/");
  await expect(secondTab.getByText("Account A", { exact: true }).first()).toBeVisible();
  await secondTab.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Warehouse Box Tracker" })).toBeVisible();
  await login(secondTab, "B");
  await expect(page.getByText("Account B", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("B-PRIVATE-INVENTORY", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("A-PRIVATE-INVENTORY", { exact: true })).toHaveCount(0);
});

test("structured password validation errors remain readable and allow retry", async ({ page, context }) => {
  await mockAPI(context, async (route) => {
    if (new URL(route.request().url()).pathname === "/api/auth/login" && route.request().postDataJSON().password.length > 72) {
      await route.fulfill({ status: 422, json: { detail: [{ loc: ["body", "password"], msg: "Password must not exceed 72 UTF-8 bytes", type: "value_error" }] } });
      return true;
    }
    return false;
  });
  await page.goto("/");
  const localTab = page.getByRole("button", { name: "Local admin", exact: true });
  if (await localTab.isVisible()) await localTab.click();
  await page.getByLabel("Username", { exact: true }).fill("account-a");
  await page.getByLabel("Password", { exact: true }).fill("a".repeat(73));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("Password must not exceed 72 UTF-8 bytes", { exact: true })).toBeVisible();
  await login(page, "A");
});

test("credential rotation adopts its replacement token before fetching the account", async ({ page, context }) => {
  let rotated = false;
  const observedTokens: string[] = [];
  await mockAPI(context, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/auth/login") {
      await route.fulfill({ json: { access_token: "bootstrap-token", token_type: "bearer", expires_in_minutes: 480, must_change_credentials: true, user: user("A", true) } });
      return true;
    }
    if (path === "/api/auth/me") {
      observedTokens.push(request.headers().authorization);
      await route.fulfill({ json: user("A", !rotated) });
      return true;
    }
    if (path === "/api/auth/change-credentials") {
      rotated = true;
      await route.fulfill({ headers: { "X-Local-Access-Token": "replacement-token" }, json: user("A") });
      return true;
    }
    return false;
  });
  await page.goto("/");
  const localTab = page.getByRole("button", { name: "Local admin", exact: true });
  if (await localTab.isVisible()) await localTab.click();
  await page.getByLabel("Username", { exact: true }).fill("account-a");
  await page.getByLabel("Password", { exact: true }).fill("bootstrap-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Set your credentials" })).toBeVisible();
  await page.getByLabel("Current password", { exact: true }).fill("bootstrap-password");
  await page.getByLabel("New username", { exact: true }).fill("rotated-admin");
  await page.getByLabel("New password (min 12 characters)", { exact: true }).fill("replacement-password");
  await page.getByLabel("Confirm new password", { exact: true }).fill("replacement-password");
  await page.getByRole("button", { name: "Save and continue" }).click();
  await expect(page.getByText("Account A", { exact: true }).first()).toBeVisible();
  expect(observedTokens.at(-1)).toBe("Bearer replacement-token");
  expect(await page.evaluate(() => localStorage.getItem("warehouse.localToken"))).toBe("replacement-token");
});

test("account loading failure offers a recoverable error instead of an indefinite spinner", async ({ page, context }) => {
  let unavailable = true;
  await context.addInitScript(() => localStorage.setItem("warehouse.localToken", "token-A"));
  await mockAPI(context, async (route) => {
    if (new URL(route.request().url()).pathname === "/api/auth/me" && unavailable) {
      await route.fulfill({ status: 503, json: { detail: "Identity provider unavailable" } });
      return true;
    }
    return false;
  });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Your account could not be loaded");
  unavailable = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByText("Account A", { exact: true }).first()).toBeVisible();
});
