import { describe, expect, it } from "vitest";
import { USER_CONFIGURATION_COLUMNS } from "./userConfiguration";

describe("desktop user configuration columns", () => {
  it("keeps every header aligned with its control field", () => {
    expect(
      USER_CONFIGURATION_COLUMNS.map(({ label, field }) => [label, field]),
    ).toEqual([
      ["User", null],
      ["Role", "role"],
      ["Override", "role_override"],
      ["Active", "is_active"],
      ["Email alerts", "email_alerts_enabled"],
      ["Request email", "email_requests_enabled"],
      ["Warehouses", "warehouse_ids"],
      ["Last login", null],
    ]);
  });
});
