import { describe, expect, it } from "vitest";
import {
  mappedEmployeeItem,
  type EmployeeColumnMapping,
} from "@/components/employeeImportMapping";
import { employeePerformanceStatus } from "./reporting";

const mapping: EmployeeColumnMapping = {
  name: 0,
  hours: 1,
  active: 2,
  excluded: 3,
};

describe("employee spreadsheet mapping", () => {
  it("maps optional hours and boolean fields", () => {
    const result = mappedEmployeeItem(
      {
        row_number: 7,
        cells: ["Alice", "7.5", "active", "excluded"],
      },
      mapping,
    );

    expect(result.item).toEqual({
      source_row: 7,
      full_name: "Alice",
      default_hours_per_day: 7.5,
      is_active: true,
      excluded_from_metrics: true,
    });
  });

  it("rejects invalid mapped hours", () => {
    const result = mappedEmployeeItem(
      { row_number: 2, cells: ["Alice", "0", "", ""] },
      mapping,
    );
    expect(result.reason).toContain("hours");
  });
});

describe("employee performance status", () => {
  it("prioritizes an explicit metrics exclusion", () => {
    expect(
      employeePerformanceStatus({
        excluded_from_metrics: true,
        consistently_below_minimum: true,
      }),
    ).toBe("excluded");
  });

  it("marks consistently below employees", () => {
    expect(
      employeePerformanceStatus({
        excluded_from_metrics: false,
        consistently_below_minimum: true,
      }),
    ).toBe("consistently-below");
  });
});
