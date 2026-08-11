import type {
  BoxLotReassignmentPayload,
  BoxStatus,
  LotFilters,
  LotForcePurgeConflict,
  LotForcePurgePayload,
  LotForcePurgePreview,
  LotForcePurgeRequestRewrite,
  LotMergeCandidate,
  LotMergePayload,
  LotOption,
  LotPurgeBlocker,
  LotPurgeBlockerCode,
  LotPurgeCleanupStatus,
  LotPurgeConflict,
  LotPurgeEntity,
  LotPurgePayload,
  LotPurgePreview,
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

export type LotMergeCandidateClassification = "normal" | "resolvable" | "hard";

export function lotMergeCandidateClassification(
  candidate: LotMergeCandidate,
): LotMergeCandidateClassification {
  if (candidate.merge_allowed) return "normal";
  if (
    candidate.merge_allowed_with_archived_overwrite &&
    candidate.requires_explicit_overwrite &&
    candidate.hard_overlap_count === 0 &&
    candidate.resolvable_archived_collision_count > 0
  ) {
    return "resolvable";
  }
  return "hard";
}

export function lotMergeConfirmationIsValid(
  candidate: LotMergeCandidate,
  reason: string,
  overwriteAcknowledged: boolean,
): boolean {
  const classification = lotMergeCandidateClassification(candidate);
  return (
    reason.trim().length > 0 &&
    reason.trim().length <= 2000 &&
    (classification === "normal" ||
      (classification === "resolvable" && overwriteAcknowledged))
  );
}

export function lotMergeConflictNeedsRefresh(code: string | null): boolean {
  return (
    code === "source_version_conflict" ||
    code === "target_version_conflict" ||
    code === "box_number_overlap" ||
    code === "overlap_signature_mismatch" ||
    code === "merge_integrity_conflict"
  );
}

export function lotMergeConflictFormState(reason: string): {
  reason: string;
  overwriteAcknowledged: false;
} {
  return { reason, overwriteAcknowledged: false };
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
  overwriteArchivedCollisions = false,
): LotMergePayload {
  const base = {
    target_lot_id: candidate.target.id,
    reason: reason.trim(),
    expected_source_version: candidate.source.version,
    expected_target_version: candidate.target.version,
  };
  if (
    !overwriteArchivedCollisions ||
    lotMergeCandidateClassification(candidate) !== "resolvable"
  ) {
    return base;
  }
  return {
    ...base,
    overwrite_archived_collisions: true,
    expected_collision_signature: candidate.collision_signature,
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

export function canPurgeLot(
  role: Role,
  lot: { id: number; state?: "merged" },
): boolean {
  return role === "admin" && !("state" in lot && lot.state === "merged");
}

export function purgeConfirmationIsValid(
  preview: LotPurgePreview | undefined,
  confirmationName: string,
  reason: string,
): boolean {
  return (
    !!preview?.eligible &&
    preview.blockers.length === 0 &&
    confirmationName === preview.lot_name &&
    reason.trim().length > 0 &&
    reason.trim().length <= 2000
  );
}

export function lotPurgePayload(
  preview: LotPurgePreview,
  confirmationName: string,
  reason: string,
): LotPurgePayload {
  return {
    confirmation_name: confirmationName,
    reason: reason.trim(),
    expected_version: preview.lot_version,
    expected_graph_signature: preview.graph_signature,
  };
}

export function lotPurgeConflict(error: unknown): LotPurgeConflict | null {
  const response = (
    error as {
      response?: {
        status?: number;
        data?: { detail?: unknown };
      };
    }
  ).response;
  const detail = response?.data?.detail;
  if (
    response?.status !== 409 ||
    typeof detail !== "object" ||
    detail === null ||
    !("code" in detail) ||
    typeof detail.code !== "string" ||
    !("message" in detail) ||
    typeof detail.message !== "string"
  ) {
    return null;
  }
  const currentPreview =
    "current_preview" in detail &&
    (typeof detail.current_preview === "object" ||
      detail.current_preview === null)
      ? (detail.current_preview as LotPurgePreview | null)
      : null;
  return {
    code: detail.code,
    message: detail.message,
    current_preview: currentPreview,
  };
}

export function purgeConflictFormState(reason: string): {
  confirmationName: string;
  reason: string;
} {
  return { confirmationName: "", reason };
}

export function purgeSuccessAction(
  status: LotPurgeCleanupStatus,
): "navigate" | "cleanup_required" {
  return status === "completed" || status === "not_required"
    ? "navigate"
    : "cleanup_required";
}

export function purgeCleanupWarning(
  auditId: number,
  status: LotPurgeCleanupStatus,
  failureCount: number,
): string {
  const failureSummary =
    failureCount === 0
      ? "Cleanup remains pending without a reported object failure."
      : `${failureCount} stored ${
          failureCount === 1 ? "object has" : "objects have"
        } not been confirmed deleted.`;
  return `The lot data is permanently gone, but storage cleanup is ${status.replaceAll(
    "_",
    " ",
  )}. ${failureSummary} Purge audit #${auditId} can be retried by an administrator.`;
}

export function lotPurgeRemovalItems(preview: LotPurgePreview): string[] {
  return [
    `Lot “${preview.lot_name}” (version ${preview.lot_version})`,
    `${preview.archived_box_count} archived ${
      preview.archived_box_count === 1 ? "box" : "boxes"
    } and their box history`,
    `${preview.linked_request_count} exclusive self-${
      preview.linked_request_count === 1 ? "receipt" : "receipts"
    } and all owned request records`,
    `${preview.object_key_count} stored ${
      preview.object_key_count === 1 ? "object" : "objects"
    }`,
  ];
}

const PURGE_BLOCKER_TEXT: Record<LotPurgeBlockerCode, string> = {
  merged_tombstone: "Merged source lots are retained as audit tombstones.",
  merge_target:
    "This lot has merged source tombstones and must remain as their target.",
  active_boxes: "Archive every active box before trying the purge again.",
  no_archived_boxes: "At least one archived box is required for this purge.",
  no_self_receipts:
    "The lot has no exclusive completed self-receipt eligible for removal.",
  staged_requests:
    "Resolve every staged or submitted receipt before trying the purge again.",
  open_requests: "Complete or resolve every open request workflow first.",
  requests_not_completed:
    "Every linked request must be completed; terminal non-completed requests block purge.",
  unsupported_request_origin:
    "Only manual-entry and XLSX-import self-receipts are supported.",
  unsupported_request_direction:
    "Return or other non-inbound requests cannot be removed with this lot.",
  mixed_lot_receipt:
    "A linked receipt contains another lot, so it is not exclusively owned by this lot.",
  incomplete_receipt_provenance:
    "Every receipt line must point to one archived physical box in this lot.",
  request_family:
    "A follow-up, return, parent, child, or request-family relationship must be retained.",
  foreign_request_reference:
    "Another request or inconsistent item still refers to this receipt graph.",
  unlinked_archived_boxes:
    "Every archived box must be traceable to one of the completed self-receipts.",
  shared_object_key:
    "Another request references the same stored object, so storage cleanup would be unsafe.",
  workflow_history:
    "The request has workflow history beyond its initial completed self-receipt event.",
  box_reassignment_history:
    "A lot or box reassignment is part of retained audit history.",
  merge_history: "Merge history must be retained and prevents purge.",
  box_workflow_history:
    "Movement, restoration, return, or status history for a box must be retained.",
};

export function lotPurgeBlockerText(blocker: LotPurgeBlocker): string {
  return PURGE_BLOCKER_TEXT[blocker.code] ?? blocker.remediation;
}

export function lotPurgeEntityPath(
  entity: LotPurgeEntity,
  currentLotId: number,
): string {
  switch (entity.entity_type) {
    case "box":
      return `/boxes/${entity.entity_id}`;
    case "request":
      return `/requests/${entity.entity_id}`;
    case "lot":
      return `/lots/${entity.entity_id}`;
    case "event":
      return `/lots/${currentLotId}#lot-audit-history`;
  }
}

export const FORCE_PURGE_MIN_REASON_LENGTH = 20;

export function shouldOfferForcePurgeEscalation(
  role: Role,
  lot: { id: number; state?: "merged" },
  safePreview: LotPurgePreview | undefined,
): boolean {
  return (
    canPurgeLot(role, lot) &&
    !!safePreview &&
    (!safePreview.eligible || safePreview.blockers.length > 0)
  );
}

export function forcePurgeHasHardBlock(
  preview: LotForcePurgePreview | undefined,
): boolean {
  return !!preview && (!preview.force_allowed || preview.hard_blockers.length > 0);
}

export function forcePurgeChange(
  before: number | null,
  after: number | null,
): string {
  return `${before ?? "—"} → ${after ?? "—"}`;
}

export function forcePurgeSiblingPreservationText(
  rewrite: LotForcePurgeRequestRewrite,
): string {
  const count = rewrite.preserved_sibling_lot_count;
  return `${count} sibling ${count === 1 ? "lot is" : "lots are"} preserved with the rewritten request.`;
}

function requiredForceBlockerCodes(
  preview: LotForcePurgePreview,
): string[] {
  return [...new Set(preview.overridden_blockers.map((blocker) => blocker.code))];
}

export function forcePurgeConfirmationIsValid(
  preview: LotForcePurgePreview | undefined,
  confirmationName: string,
  confirmationPhrase: string,
  reason: string,
  acknowledgedBlockerCodes: readonly string[],
): boolean {
  if (!preview || forcePurgeHasHardBlock(preview)) return false;
  const requiredCodes = requiredForceBlockerCodes(preview);
  const suppliedCodes = [...new Set(acknowledgedBlockerCodes)];
  return (
    confirmationName === preview.lot_name &&
    confirmationPhrase === preview.confirmation_phrase &&
    reason.trim().length >= FORCE_PURGE_MIN_REASON_LENGTH &&
    reason.trim().length <= 2000 &&
    suppliedCodes.length === acknowledgedBlockerCodes.length &&
    suppliedCodes.length === requiredCodes.length &&
    requiredCodes.every((code) => suppliedCodes.includes(code))
  );
}

export function lotForcePurgePayload(
  preview: LotForcePurgePreview,
  confirmationName: string,
  confirmationPhrase: string,
  reason: string,
  acknowledgedBlockerCodes: readonly string[],
): LotForcePurgePayload {
  return {
    confirmation_name: confirmationName,
    confirmation_phrase: confirmationPhrase,
    reason: reason.trim(),
    expected_version: preview.lot_version,
    expected_graph_signature: preview.graph_signature,
    acknowledged_blocker_codes: [...acknowledgedBlockerCodes].sort(),
  };
}

export function lotForcePurgeConflict(
  error: unknown,
): LotForcePurgeConflict | null {
  const response = (
    error as {
      response?: {
        status?: number;
        data?: { detail?: unknown };
      };
    }
  ).response;
  const detail = response?.data?.detail;
  if (
    response?.status !== 409 ||
    typeof detail !== "object" ||
    detail === null ||
    !("code" in detail) ||
    typeof detail.code !== "string" ||
    !("message" in detail) ||
    typeof detail.message !== "string"
  ) {
    return null;
  }
  const currentPreview =
    "current_preview" in detail &&
    (typeof detail.current_preview === "object" ||
      detail.current_preview === null)
      ? (detail.current_preview as LotForcePurgePreview | null)
      : null;
  return {
    code: detail.code,
    message: detail.message,
    current_preview: currentPreview,
  };
}

export function forcePurgeConflictFormState(reason: string): {
  confirmationName: string;
  confirmationPhrase: string;
  reason: string;
  acknowledgedBlockerCodes: string[];
} {
  return {
    confirmationName: "",
    confirmationPhrase: "",
    reason,
    acknowledgedBlockerCodes: [],
  };
}

