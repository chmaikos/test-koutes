import type {
  BoxFilters,
  PalletFilters,
  PalletBoxMutationPayload,
  PalletCreatePayload,
  PalletOption,
  PalletOptionFilters,
  PalletProgressState,
  PalletSortField,
  Role,
} from "@/api/types";

const PROGRESS = new Set<PalletProgressState>([
  "active",
  "in_progress",
  "complete",
  "no_eligible",
]);
const SORTS = new Set<PalletSortField>([
  "pallet_number",
  "completion",
  "box_count",
  "latest_activity",
]);

export const PALLET_PROGRESS_LABELS: Record<PalletProgressState, string> = {
  active: "Active",
  in_progress: "In progress",
  complete: "Complete",
  no_eligible: "No eligible boxes",
};

export function normalizePalletNumber(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

export function exactPalletMatch(
  options: PalletOption[],
  value: string,
): PalletOption | undefined {
  const normalized = normalizePalletNumber(value);
  return options.find(
    (option) => option.normalized_pallet_number === normalized,
  );
}

export function parsePalletSearchParams(params: URLSearchParams): {
  filters: PalletFilters;
  page: number;
  pageSize: number;
} {
  const filters: PalletFilters = {};
  const search = params.get("q")?.trim();
  const lotId = Number(params.get("lot_id"));
  const progress = params.get("progress") as PalletProgressState | null;
  const sort = params.get("sort_by") as PalletSortField | null;
  if (search) filters.search = search;
  if (lotId > 0) filters.lot_id = lotId;
  if (progress && PROGRESS.has(progress)) filters.progress_state = progress;
  if (params.get("include_inactive") === "true") filters.include_inactive = true;
  if (sort && SORTS.has(sort)) filters.sort_by = sort;
  filters.sort_dir = params.get("sort_dir") === "asc" ? "asc" : "desc";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const pageSizeValue = Number(params.get("page_size")) || 25;
  const pageSize = [25, 50, 100, 200].includes(pageSizeValue)
    ? pageSizeValue
    : 25;
  return { filters, page, pageSize };
}

export function palletCompletionLabel(value: number | null): string {
  return value === null ? "N/A" : `${Math.round(value)}%`;
}

export function palletScopeKey(lotId: number | undefined): string {
  return `${lotId ?? "none"}`;
}

export function isValidPalletPickerValue(
  numberValue: string | null | undefined,
  selection: { id?: number; pallet_number: string } | null,
  required = false,
): boolean {
  const normalized = normalizePalletNumber(numberValue ?? "");
  if (!normalized) return !required;
  return (
    selection !== null &&
    normalizePalletNumber(selection.pallet_number) === normalized
  );
}

export function palletPickerOptionFilters(
  lotId: number | undefined,
  search: string,
  includeInactive: boolean,
): PalletOptionFilters {
  return {
    lot_id: lotId,
    search: search || undefined,
    include_inactive: includeInactive,
  };
}

export function palletCandidateFilters(
  lotId: number,
  warehouseId?: number,
  palletId?: number,
): BoxFilters {
  return {
    lot_id: lotId,
    warehouse_id: warehouseId,
    pallet_id: palletId,
    sort_by: "updated_at",
    sort_dir: "desc",
  };
}

export function palletWarehouseDistributionLabel(
  warehouseNames: string[],
): string {
  return warehouseNames.length
    ? `Boxes currently in ${warehouseNames.join(", ")}`
    : "No boxes in accessible warehouses";
}

export function palletOptionSelection(option: PalletOption): {
  id: number;
  pallet_number: string;
} {
  return { id: option.id, pallet_number: option.pallet_number };
}

export function canManagePalletBoxes(role: Role): boolean {
  return role === "admin" || role === "operator";
}

export function canAdministerPallet(role: Role): boolean {
  return role === "admin";
}

export function unassignedPalletLabel(
  palletNumber: string | null | undefined,
): string {
  return palletNumber || "Unassigned";
}

export function palletCreatePayload(
  lotId: number,
  warehouseId: number,
  palletNumber: string,
): PalletCreatePayload {
  return {
    lot_id: lotId,
    warehouse_id: warehouseId,
    pallet_number: palletNumber.trim().replace(/\s+/g, " "),
  };
}

export function palletBoxMutationPayload(
  boxIds: number[],
  reason: string,
): PalletBoxMutationPayload {
  return {
    box_ids: [...new Set(boxIds)],
    reason: reason.trim(),
  };
}
