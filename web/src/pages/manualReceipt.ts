import type { FileInput } from "@/api/types";
import { cleanFileItems } from "@/components/fileItems";

export interface ManualReceiptPallet {
  id: number;
  pallet_number: string;
}

export function manualReceiptPayload(input: {
  boxNumber: string;
  lotId: number;
  pallet: ManualReceiptPallet | null;
  files: FileInput[];
  warehouseId: number;
}) {
  const trimmedNumber = input.boxNumber.trim();
  const normalizedNumber = /^\d+$/.test(trimmedNumber)
    ? trimmedNumber.padStart(3, "0")
    : trimmedNumber;
  return {
    box_number: normalizedNumber,
    lot_id: input.lotId,
    ...(input.pallet
      ? {
          pallet_id: input.pallet.id,
          pallet_number: input.pallet.pallet_number,
        }
      : {}),
    files: cleanFileItems(input.files),
    warehouse_id: input.warehouseId,
  };
}
