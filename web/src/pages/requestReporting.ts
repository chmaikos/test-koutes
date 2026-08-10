import type { RequestReportFilters } from "@/api/types";

export function requestReportDateRange(from: string, to: string) {
  const range: Pick<RequestReportFilters, "from_at" | "to_at"> = {};
  if (from) range.from_at = new Date(`${from}T00:00:00`).toISOString();
  if (to) {
    const exclusiveEnd = new Date(`${to}T00:00:00`);
    exclusiveEnd.setDate(exclusiveEnd.getDate() + 1);
    range.to_at = exclusiveEnd.toISOString();
  }
  return range;
}
