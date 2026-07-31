import type { RequestOrigin, ReturnCandidate } from "@/api/types";

export function returnCandidateIds(candidates: ReturnCandidate[]): number[] {
  return candidates.map((candidate) => candidate.box_id);
}

export function toggleReturnBox(
  selectedBoxIds: number[],
  boxId: number,
): number[] {
  return selectedBoxIds.includes(boxId)
    ? selectedBoxIds.filter((selectedId) => selectedId !== boxId)
    : [...selectedBoxIds, boxId];
}

export function canSubmitReturnSelection(
  sourceInboundRequestId: number | undefined,
  selectedBoxIds: number[],
): boolean {
  return sourceInboundRequestId !== undefined && selectedBoxIds.length > 0;
}

export function returnSourceLabel(origin: RequestOrigin): string {
  switch (origin) {
    case "xlsx_import":
      return "Imported receipt";
    case "manual_entry":
      return "Manual receipt";
    case "legacy_backfill":
      return "Legacy inventory receipt";
    default:
      return "Inbound order";
  }
}
