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

export function importResultTitle(result: ImportResult): string {
  const batchSuffix = result.receipt_request_ids.length === 1 ? "" : "es";
  const staged = result.staged_receipt_ids?.length ?? 0;
  const stagedLabel = staged ? ` · ${staged} staged` : "";
  return `Import result · ${result.created.length} created · ${result.restored.length} restored${stagedLabel} · ${result.receipt_request_ids.length} receipt batch${batchSuffix}`;
}
