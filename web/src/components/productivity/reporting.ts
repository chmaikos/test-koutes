import type { EmployeeAverage } from "@/api/types";

export type EmployeePerformanceStatus =
  | "excluded"
  | "consistently-below"
  | "normal";

export function employeePerformanceStatus(
  employee: Pick<
    EmployeeAverage,
    "excluded_from_metrics" | "consistently_below_minimum"
  >,
): EmployeePerformanceStatus {
  if (employee.excluded_from_metrics) return "excluded";
  if (employee.consistently_below_minimum) return "consistently-below";
  return "normal";
}
