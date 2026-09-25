import type {
  RequestOrigin,
  ReturnCandidate,
  ReturnLotContext,
} from "@/api/types";

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

export function palletCandidateIds(
  candidates: ReturnCandidate[],
  palletId: number | null,
): number[] {
  return candidates
    .filter((candidate) => candidate.pallet_id === palletId)
    .map((candidate) => candidate.box_id);
}

export function toggleReturnPallet(
  selectedBoxIds: number[],
  candidates: ReturnCandidate[],
  palletId: number | null,
): number[] {
  const palletIds = palletCandidateIds(candidates, palletId);
  const allSelected =
    palletIds.length > 0 && palletIds.every((id) => selectedBoxIds.includes(id));
  return allSelected
    ? selectedBoxIds.filter((id) => !palletIds.includes(id))
    : [...new Set([...selectedBoxIds, ...palletIds])];
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

export function returnSourceLotsLabel(lots: ReturnLotContext[]): string {
  if (lots.length === 0) return "Lot unavailable";
  const names = lots.map((lot) => lot.lot_name);
  return `${names.length === 1 ? "Lot" : "Lots"} ${names.join(", ")}`;
}
