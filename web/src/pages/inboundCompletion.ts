import type {
  InboundCompletionPreview,
  InboundCompletionPreviewRow,
  InboundRequestItemInput,
  ReviewedInboundCompletionFields,
} from "@/api/types";
import { normalizeLotName } from "@/pages/lots";
import { normalizePalletNumber } from "@/pages/pallets";
import { tryGroupInboundItems } from "@/pages/xlsxMapping";

interface LotConfirmation {
  id: number;
  name: string;
}

interface PalletConfirmation {
  id: number;
  pallet_number: string;
}

export interface MappedInboundAcceptance {
  lots: ReadonlySet<string>;
  pallets: ReadonlySet<string>;
}

export function mappedInboundAcceptance(
  rows: InboundRequestItemInput[],
): MappedInboundAcceptance {
  return {
    lots: new Set(
      rows.flatMap((row) => {
        const lot = normalizeLotName(row.lot);
        return lot ? [lot] : [];
      }),
    ),
    pallets: new Set(
      rows.flatMap((row) => {
        const lot = normalizeLotName(row.lot);
        const pallet = normalizePalletNumber(row.pallet_number ?? "");
        return lot && pallet ? [`${lot}\u0000${pallet}`] : [];
      }),
    ),
  };
}

export function acceptsMappedLot(
  row: InboundRequestItemInput,
  acceptance: MappedInboundAcceptance,
): boolean {
  return acceptance.lots.has(normalizeLotName(row.lot));
}

export function acceptsMappedPallet(
  row: InboundRequestItemInput,
  acceptance: MappedInboundAcceptance,
): boolean {
  const lot = normalizeLotName(row.lot);
  const pallet = normalizePalletNumber(row.pallet_number ?? "");
  return !!lot && !!pallet && acceptance.pallets.has(`${lot}\u0000${pallet}`);
}

export interface InboundCompletionReviewState {
  preview: InboundCompletionPreview | null;
  fingerprint: string | null;
  acceptRelocations: boolean;
  acceptFileMoves: boolean;
  notice: string | null;
}

export const EMPTY_INBOUND_COMPLETION_REVIEW: InboundCompletionReviewState = {
  preview: null,
  fingerprint: null,
  acceptRelocations: false,
  acceptFileMoves: false,
  notice: null,
};

export function inboundCompletionItems(
  rows: InboundRequestItemInput[],
  palletConfirmations: Record<number, PalletConfirmation | null>,
): ReturnType<typeof tryGroupInboundItems> {
  return tryGroupInboundItems(
    rows.flatMap((row, index) =>
      row.lot.trim() && row.box_number.trim()
        ? [
            {
              lot: row.lot.trim().replace(/\s+/g, " "),
              box_number: row.box_number.trim(),
              pallet_number:
                row.pallet_number?.trim().replace(/\s+/g, " ") || null,
              ...(row.pallet_number?.trim() && palletConfirmations[index]?.id
                ? { pallet_id: palletConfirmations[index]!.id }
                : {}),
              contents: row.contents?.trim() || undefined,
              files: row.files?.map((file) => ({
                reference: file.reference.trim().replace(/\s+/g, " "),
                description: file.description?.trim() || undefined,
              })),
            },
          ]
        : [],
    ),
  );
}

export function inboundCompletionFingerprint(input: {
  requestId: number;
  requestVersion: number;
  rows: InboundRequestItemInput[];
  lotConfirmations: Record<number, LotConfirmation | null>;
  palletConfirmations: Record<number, PalletConfirmation | null>;
}): string {
  return JSON.stringify({
    requestId: input.requestId,
    requestVersion: input.requestVersion,
    rows: input.rows.map((row, index) => ({
      lot: row.lot.trim().replace(/\s+/g, " "),
      boxNumber: row.box_number.trim(),
      palletNumber:
        row.pallet_number?.trim().replace(/\s+/g, " ") || null,
      palletId:
        row.pallet_number?.trim()
          ? (input.palletConfirmations[index]?.id ?? row.pallet_id ?? null)
          : null,
      contents: row.contents?.trim() || null,
      files:
        row.files?.map((file) => ({
          reference: file.reference.trim().replace(/\s+/g, " "),
          description: file.description?.trim() || null,
        })) ?? null,
      lotConfirmation: input.lotConfirmations[index]
        ? {
            id: input.lotConfirmations[index]!.id,
            name: input.lotConfirmations[index]!.name,
          }
        : null,
      palletConfirmation: input.palletConfirmations[index]
        ? {
            id: input.palletConfirmations[index]!.id,
            number: input.palletConfirmations[index]!.pallet_number,
          }
        : null,
    })),
  });
}

export function inboundTargetPalletLabel(
  row: InboundCompletionPreviewRow,
): string {
  const target = row.target_pallet_resolution;
  if (target.resolution === "preserve_existing") {
    return row.current_pallet_number
      ? `Keep current pallet ${row.current_pallet_number}`
      : "Remain Unassigned";
  }
  if (target.resolution === "unassigned") return "Unassigned";
  return target.pallet_number ?? "Unassigned";
}

export function reviewedInboundCompletion(
  preview: InboundCompletionPreview,
  fingerprint: string,
): InboundCompletionReviewState {
  return {
    preview,
    fingerprint,
    acceptRelocations: false,
    acceptFileMoves: false,
    notice: null,
  };
}

export function invalidateInboundCompletion(
  notice: string | null = null,
): InboundCompletionReviewState {
  return {
    ...EMPTY_INBOUND_COMPLETION_REVIEW,
    notice,
  };
}

export function setInboundRelocationAcceptance(
  state: InboundCompletionReviewState,
  accepted: boolean,
): InboundCompletionReviewState {
  return {
    ...state,
    acceptRelocations:
      (state.preview?.summary.relocated ?? 0) > 0 && accepted,
  };
}

export function setInboundFileMoveAcceptance(
  state: InboundCompletionReviewState,
  accepted: boolean,
): InboundCompletionReviewState {
  return {
    ...state,
    acceptFileMoves:
      (state.preview?.summary.files_moved ?? 0) > 0 && accepted,
  };
}

export function currentInboundCompletionPreview(
  state: InboundCompletionReviewState,
  fingerprint: string,
  requestVersion: number,
): InboundCompletionPreview | null {
  if (
    state.fingerprint !== fingerprint ||
    state.preview?.request_version !== requestVersion
  ) {
    return null;
  }
  return state.preview;
}

export function canSubmitInboundCompletion(
  preview: InboundCompletionPreview | null,
  acceptRelocations: boolean,
  acceptFileMoves = false,
): boolean {
  if (!preview || !preview.can_complete || preview.summary.blocked > 0) {
    return false;
  }
  return (
    (preview.summary.relocated === 0 || acceptRelocations) &&
    (preview.summary.files_moved === 0 || acceptFileMoves)
  );
}

export function inboundCompletionFields(
  preview: InboundCompletionPreview,
  acceptRelocations: boolean,
  acceptFileMoves = false,
): ReviewedInboundCompletionFields {
  return {
    inbound_impact_signature: preview.impact_signature,
    accept_existing_received_boxes:
      preview.summary.relocated > 0 && acceptRelocations,
    accept_file_moves:
      preview.summary.files_moved > 0 && acceptFileMoves,
  };
}

export function inboundCompletionCounts(
  ordered: number,
  preview: InboundCompletionPreview,
  acceptRelocations: boolean,
) {
  const eligible = preview.summary.created + preview.summary.relocated;
  return {
    created: preview.summary.created,
    relocated: preview.summary.relocated,
    blocked: preview.summary.blocked,
    eligible,
    accepted:
      preview.summary.created +
      (acceptRelocations ? preview.summary.relocated : 0),
    variance: eligible - ordered,
  };
}

export function inboundBlockedMessage(
  row: InboundCompletionPreviewRow,
): string {
  if (row.blocked_message) return row.blocked_message;
  const messages: Record<string, string> = {
    archived_identity: "This box identity is archived.",
    existing_at_target: "This box already exists in the request warehouse.",
    invalid_status: "Only received boxes can be relocated.",
    active_return_reservation:
      "This box is reserved by an active return request.",
    target_pallet_archived: "The target pallet is archived.",
    target_pallet_identity_mismatch:
      "The selected pallet does not match its pallet number.",
    target_pallet_lot_mismatch:
      "The selected pallet belongs to another lot.",
    target_pallet_not_found: "The selected target pallet no longer exists.",
  };
  return messages[row.blocked_code ?? ""] ?? "This row cannot be completed.";
}

export function isStaleInboundImpactConflict(error: unknown): boolean {
  const response = (
    error as {
      response?: { status?: number; data?: { detail?: unknown } };
    }
  )?.response;
  if (response?.status !== 409) return false;
  const detail = response.data?.detail;
  const message =
    typeof detail === "string"
      ? detail
      : detail &&
          typeof detail === "object" &&
          "message" in detail &&
          typeof detail.message === "string"
        ? detail.message
        : "";
  return (
    message.includes("inbound impact changed") ||
    message.includes("request changed; refresh and retry")
  );
}
