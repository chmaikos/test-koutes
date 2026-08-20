export interface ManualReceiptPallet {
  id: number;
  pallet_number: string;
}

export function manualReceiptPayload(input: {
  boxNumber: string;
  lotId: number;
  pallet: ManualReceiptPallet | null;
  contents: string;
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
    contents: input.contents.trim() || undefined,
    warehouse_id: input.warehouseId,
  };
}
