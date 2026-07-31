import type { EmployeeImportItem, XlsxPreviewRow } from "@/api/types";

type MappingKey = "name" | "hours" | "active" | "excluded";
export type EmployeeColumnMapping = Record<MappingKey, number | null>;

function parseBoolean(
  value: string,
  mode: "active" | "excluded",
): boolean | undefined | null {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return undefined;
  if (["true", "yes", "1"].includes(normalized)) return true;
  if (["false", "no", "0"].includes(normalized)) return false;
  if (mode === "active" && normalized === "active") return true;
  if (mode === "active" && normalized === "inactive") return false;
  if (mode === "excluded" && normalized === "excluded") return true;
  if (mode === "excluded" && normalized === "included") return false;
  return null;
}

export function employeeImportCell(
  row: XlsxPreviewRow,
  column: number | null,
): string {
  return column === null ? "" : (row.cells[column] ?? "").trim();
}

export function mappedEmployeeItem(
  row: XlsxPreviewRow,
  mapping: EmployeeColumnMapping,
): { item?: EmployeeImportItem; reason?: string } {
  const fullName = employeeImportCell(row, mapping.name);
  if (!fullName) return { reason: "employee name is empty" };

  const hoursText = employeeImportCell(row, mapping.hours);
  const hours = hoursText ? Number(hoursText) : undefined;
  if (
    hours !== undefined &&
    (!Number.isFinite(hours) || hours <= 0 || hours > 24)
  ) {
    return { reason: "hours must be greater than 0 and at most 24" };
  }

  const active = parseBoolean(
    employeeImportCell(row, mapping.active),
    "active",
  );
  if (active === null) return { reason: "active value is not recognized" };
  const excluded = parseBoolean(
    employeeImportCell(row, mapping.excluded),
    "excluded",
  );
  if (excluded === null) return { reason: "excluded value is not recognized" };

  return {
    item: {
      source_row: row.row_number,
      full_name: fullName,
      ...(hours === undefined ? {} : { default_hours_per_day: hours }),
      ...(active === undefined ? {} : { is_active: active }),
      ...(excluded === undefined ? {} : { excluded_from_metrics: excluded }),
    },
  };
}
