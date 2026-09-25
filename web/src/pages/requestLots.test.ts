import { describe, expect, it } from "vitest";
import type { BoxRequestItem } from "@/api/types";
import { requestLots } from "@/pages/requestLots";

function item(
  id: number,
  lotId: number | null,
  lot: string | null,
): BoxRequestItem {
  return {
    id,
    box_id: null,
    lot_id: lotId,
    lot,
    pallet_id: null,
    pallet: null,
    pallet_number: null,
    box_number: null,
    contents: null,
    lot_barcode: null,
    pallet_barcode: null,
    box_barcode: null,
    files: [],
  };
}

describe("requestLots", () => {
  it("deduplicates linked lots and sorts their labels", () => {
    expect(
      requestLots({
        items: [
          item(1, 2, "Lot 10"),
          item(2, 1, "Lot 2"),
          item(3, 1, "Lot 2"),
        ],
      }),
    ).toEqual([
      { key: "id:1", id: 1, name: "Lot 2" },
      { key: "id:2", id: 2, name: "Lot 10" },
    ]);
  });

  it("keeps unlinked historical lot snapshots", () => {
    expect(
      requestLots({
        items: [item(1, null, "Legacy lot"), item(2, null, null)],
      }),
    ).toEqual([{ key: "name:Legacy lot", id: null, name: "Legacy lot" }]);
  });
});
