import type {
  BoxLotReassignmentPayload,
  BoxStatus,
  LotFilters,
  LotMergeCandidate,
  LotMergePayload,
  LotOption,
  LotProgressState,
  LotRenamePayload,
  LotSortField,
  LotStatusCounts,
  Role,
} from "@/api/types";

export const LOT_PROGRESS_LABELS: Record<LotProgressState, string> = {
  active: "Active",
  in_progress: "In progress",
  complete: "Complete",
  no_eligible: "No eligible boxes",
};

export const LOT_SORT_FIELDS = new Set<LotSortField>([
  "name",
  "completion",
  "box_count",
  "last_activity",
]);

export function normalizeLotName(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

export function exactLotMatch(
  options: LotOption[],
  value: string,
): LotOption | undefined {
  const normalized = normalizeLotName(value);
  return options.find((option) => option.normalized_name === normalized);
}

export function shouldOfferLotCreation(
  options: LotOption[],
  value: string,
  authorized: boolean,
  warehouseId?: number,
): boolean {
  return (
    authorized &&
    !!warehouseId &&
    normalizeLotName(value).length > 0 &&
    !exactLotMatch(options, value)
  );
}

export function lotSelection(option: Pick<LotOption, "id" | "name">) {
  return { id: option.id, name: option.name };
}

export function completionLabel(percent: number | null): string {
  return percent === null ? "No eligible boxes" : `${percent.toFixed(1)}%`;
}

export function parseLotSearchParams(params: URLSearchParams): {
  filters: LotFilters;
  page: number;
  pageSize: number;
} {
  const filters: LotFilters = {};
  const search = params.get("q")?.trim();
  if (search) filters.search = search;
  const warehouseId = Number(params.get("warehouse_id"));
  if (Number.isInteger(warehouseId) && warehouseId > 0) {
    filters.warehouse_id = warehouseId;
  }
  const progress = params.get("progress");
  if (
    progress === "active" ||
    progress === "in_progress" ||
    progress === "complete" ||
    progress === "no_eligible"
  ) {
    filters.progress_state = progress;
  }
  const sortBy = params.get("sort_by");
  if (sortBy && LOT_SORT_FIELDS.has(sortBy as LotSortField)) {
    filters.sort_by = sortBy as LotSortField;
    filters.sort_dir = params.get("sort_dir") === "asc" ? "asc" : "desc";
  }
  const page = Math.max(1, Number(params.get("page")) || 1);
  const requestedSize = Number(params.get("page_size"));
  const pageSize = [25, 50, 100, 200].includes(requestedSize)
    ? requestedSize
    : 25;
  return { filters, page, pageSize };
}

export interface LotStatusSegment {
  status: BoxStatus;
  count: number;
  percent: number;
}

export function lotStatusSegments(
  counts: LotStatusCounts,
): LotStatusSegment[] {
  const statuses: BoxStatus[] = [
    "quarantined",
    "received",
    "processing",
    "incomplete",
    "ready_to_return",
    "returned",
  ];
  const total = statuses.reduce((sum, status) => sum + counts[status], 0);
  return statuses
    .filter((status) => counts[status] > 0)
    .map((status) => ({
      status,
      count: counts[status],
      percent: total === 0 ? 0 : (counts[status] / total) * 100,
    }));
}

export function renameValidation(name: string, reason: string): string | null {
  if (!name.trim()) return "Enter a new lot name.";
  if (!reason.trim()) return "A correction reason is required.";
  return null;
}

export interface LotConflictCurrent {
  id: number;
  name: string;
  normalized_name: string;
  version: number;
  updated_at: string;
}

export function lotConflictCurrent(error: unknown): LotConflictCurrent | null {
  const detail = (
    error as {
      response?: {
        status?: number;
        data?: {
          detail?: {
            code?: string;
            current?: LotConflictCurrent | null;
          };
        };
      };
    }
  ).response;
  return detail?.status === 409 &&
    detail.data?.detail?.code === "version_conflict"
    ? (detail.data.detail.current ?? null)
    : null;
}

export function lotMergeCandidate(error: unknown): LotMergeCandidate | null {
  const response = (
    error as {
      response?: {
        status?: number;
        data?: {
          detail?: {
            code?: string;
            merge_candidate?: LotMergeCandidate | null;
          };
        };
      };
    }
  ).response;
  return response?.status === 409
    ? (response.data?.detail?.merge_candidate ?? null)
    : null;
}

export function lotMergeConflictCode(error: unknown): string | null {
  const response = (
    error as {
      response?: {
        status?: number;
        data?: { detail?: { code?: unknown } };
      };
    }
  ).response;
  return response?.status === 409 &&
    typeof response.data?.detail?.code === "string"
    ? response.data.detail.code
    : null;
}

export function lotRenamePayload(
  name: string,
  reason: string,
  version: number,
): LotRenamePayload {
  return {
    new_name: name.trim().replace(/\s+/g, " "),
    reason: reason.trim(),
    expected_version: version,
  };
}

export function lotMergePayload(
  candidate: LotMergeCandidate,
  reason: string,
): LotMergePayload {
  return {
    target_lot_id: candidate.target.id,
    reason: reason.trim(),
    expected_source_version: candidate.source.version,
    expected_target_version: candidate.target.version,
  };
}

export function canReassignLot(role: Role): boolean {
  return role === "admin";
}

export function reassignmentPayload(
  lotId: number,
  reason: string,
  sourceLotVersion: number,
): BoxLotReassignmentPayload {
  return {
    lot_id: lotId,
    reason: reason.trim(),
    expected_lot_version: sourceLotVersion,
  };
}

