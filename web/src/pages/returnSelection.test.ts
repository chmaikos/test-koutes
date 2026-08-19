import { describe, expect, it } from "vitest";
import type { ReturnCandidate } from "@/api/types";
import {
  canSubmitReturnSelection,
  returnCandidateIds,
  palletCandidateIds,
  returnSourceLabel,
  toggleReturnBox,
  toggleReturnPallet,
} from "@/pages/returnSelection";

const candidates: ReturnCandidate[] = [
  {
    box_id: 11,
    box_number: "001",
    lot: "LOT-A",
    lot_id: 21,
    pallet_id: 5,
    pallet_number: "PAL-5",
    contents: "A",
    status: "ready_to_return",
  },
  {
    box_id: 12,
    box_number: "002",
    lot: "LOT-A",
    lot_id: 21,
    pallet_id: null,
    pallet_number: null,
    contents: null,
    status: "ready_to_return",
  },
];

describe("partial return selection", () => {
  it("selects all candidates and derives quantity from the selection", () => {
    const selected = returnCandidateIds(candidates);
    expect(selected).toEqual([11, 12]);
    expect(selected).toHaveLength(2);
  });

  it("supports deselecting and reselecting individual boxes", () => {
    expect(toggleReturnBox([11, 12], 11)).toEqual([12]);
    expect(toggleReturnBox([12], 11)).toEqual([12, 11]);
  });

  it("blocks empty or source-less returns", () => {
    expect(canSubmitReturnSelection(undefined, [11])).toBe(false);
    expect(canSubmitReturnSelection(42, [])).toBe(false);
    expect(canSubmitReturnSelection(42, [11])).toBe(true);
  });

  it("expands pallet groups without forcing full-pallet returns", () => {
    expect(palletCandidateIds(candidates, 5)).toEqual([11]);
    expect(toggleReturnPallet([], candidates, 5)).toEqual([11]);
    expect(toggleReturnPallet([11, 12], candidates, 5)).toEqual([12]);
  });

  it("labels workflow and generated receipt sources clearly", () => {
    expect(returnSourceLabel("workflow")).toBe("Inbound order");
    expect(returnSourceLabel("xlsx_import")).toBe("Imported receipt");
    expect(returnSourceLabel("manual_entry")).toBe("Manual receipt");
    expect(returnSourceLabel("legacy_backfill")).toBe(
      "Legacy inventory receipt",
    );
  });
});
