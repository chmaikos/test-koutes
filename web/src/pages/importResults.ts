import type { ImportResult, InboundRequestItemInput } from "@/api/types";

export function mappedImportPayload(
  warehouseId: number,
  items: InboundRequestItemInput[],
  restoreArchived: boolean,
) {
  return {
    warehouse_id: warehouseId,
    items,
    restore_archived: restoreArchived,
  };
}

export function palletAssignmentCounts(
  items: Array<{ pallet_number?: string | null; pallet_id?: number | null }>,
) {
  const assigned = items.filter(
    (item) => item?.pallet_id != null || !!item?.pallet_number?.trim(),
  ).length;
  return { assigned, unassigned: items.length - assigned };
}

export function importResultTitle(result: ImportResult): string {
  const batchSuffix = result.receipt_request_ids.length === 1 ? "" : "es";
  const staged = result.staged_receipt_ids?.length ?? 0;
  const stagedLabel = staged ? ` · ${staged} staged` : "";
  const pallets = palletAssignmentCounts([
    ...result.created,
    ...result.restored,
  ]);
  return `Import result · ${result.created.length} created · ${result.restored.length} restored${stagedLabel} · ${pallets.assigned} assigned · ${pallets.unassigned} Unassigned · ${result.receipt_request_ids.length} receipt batch${batchSuffix}`;
}
