import type {
  FileActivity,
  FileSortField,
  TrackedFileFilters,
} from "@/api/types";

export const FILE_SORT_FIELDS: ReadonlySet<FileSortField> = new Set([
  "reference",
  "lot",
  "pallet",
  "box",
  "warehouse",
  "status",
  "position",
  "created_at",
  "updated_at",
]);

export function parseFileSearchParams(params: URLSearchParams): {
  filters: TrackedFileFilters;
  page: number;
  pageSize: number;
} {
  const filters: TrackedFileFilters = {};
  const positiveNumber = (name: string) => {
    const value = Number(params.get(name));
    return Number.isInteger(value) && value > 0 ? value : undefined;
  };
  const search = params.get("q")?.trim();
  if (search) filters.search = search;
  for (const key of ["warehouse_id", "lot_id", "pallet_id", "box_id"] as const) {
    const value = positiveNumber(key);
    if (value) filters[key] = value;
  }
  const status = params.get("status");
  if (
    status &&
    ["quarantined", "received", "processing", "incomplete", "ready_to_return", "returned"].includes(status)
  ) {
    filters.status = status as TrackedFileFilters["status"];
  }
  const activity = params.get("activity");
  if (["active", "archived", "all"].includes(activity ?? "")) {
    filters.activity = activity as FileActivity;
  }
  if (params.get("include_inactive") === "true") filters.include_inactive = true;
  const sortBy = params.get("sort_by");
  if (FILE_SORT_FIELDS.has(sortBy as FileSortField)) {
    filters.sort_by = sortBy as FileSortField;
    filters.sort_dir = params.get("sort_dir") === "desc" ? "desc" : "asc";
  }
  const page = positiveNumber("page") ?? 1;
  const requestedSize = positiveNumber("page_size") ?? 25;
  const pageSize = [25, 50, 100, 200].includes(requestedSize)
    ? requestedSize
    : 25;
  return { filters, page, pageSize };
}
