import { describe, expect, it } from "vitest";
import { requestReportDateRange } from "./requestReporting";

describe("requestReportDateRange", () => {
  it("uses local midnight and an exclusive next-day boundary", () => {
    const result = requestReportDateRange("2026-08-10", "2026-08-10");
    const start = new Date("2026-08-10T00:00:00");
    const end = new Date("2026-08-10T00:00:00");
    end.setDate(end.getDate() + 1);

    expect(result.from_at).toBe(start.toISOString());
    expect(result.to_at).toBe(end.toISOString());
  });

  it("omits unavailable boundaries", () => {
    expect(requestReportDateRange("", "")).toEqual({});
  });
});
