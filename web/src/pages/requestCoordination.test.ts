import { describe, expect, it } from "vitest";
import {
  toLocalDateTimeInput,
  toUtcDateTime,
  validTransportWindow,
} from "@/pages/requestCoordination";

describe("request coordination date handling", () => {
  it("round-trips persisted timestamps through datetime-local values", () => {
    const persisted = "2026-08-11T09:30:00.000Z";
    const local = toLocalDateTimeInput(persisted);
    expect(toUtcDateTime(local)).toBe(persisted);
  });

  it("uses null to explicitly clear an optional timestamp", () => {
    expect(toUtcDateTime("")).toBeNull();
    expect(toLocalDateTimeInput(null)).toBe("");
  });

  it("requires an ordered transport window", () => {
    expect(validTransportWindow("", "")).toBe(true);
    expect(validTransportWindow("", "2026-08-11T10:00")).toBe(false);
    expect(
      validTransportWindow("2026-08-11T10:00", "2026-08-11T09:00"),
    ).toBe(false);
    expect(
      validTransportWindow("2026-08-11T09:00", "2026-08-11T10:00"),
    ).toBe(true);
  });
});
