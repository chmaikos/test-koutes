import { describe, expect, it } from "vitest";
import type { PalletOption, Role } from "@/api/types";
import {
  canAdministerPallet,
  canManagePalletBoxes,
  exactPalletMatch,
  normalizePalletNumber,
  palletOptionSelection,
  palletCreatePayload,
  palletBoxMutationPayload,
  palletScopeKey,
  parsePalletSearchParams,
  unassignedPalletLabel,
} from "@/pages/pallets";

const option: PalletOption = {
  id: 7,
  pallet_number: "PAL 01",
  normalized_pallet_number: "pal 01",
  lot_id: 2,
  lot_name: "LOT-2",
  current_warehouse_id: 3,
  warehouse_name: "North",
  is_active: true,
  exact_normalized_match: false,
};

describe("pallet list and picker helpers", () => {
  it("parses the pallet list route filters and pagination", () => {
    const parsed = parsePalletSearchParams(
      new URLSearchParams(
        "q=PAL&warehouse_id=3&lot_id=2&progress=in_progress&include_inactive=true&sort_by=completion&sort_dir=asc&page=2&page_size=50",
      ),
    );
    expect(parsed).toEqual({
      filters: {
        search: "PAL",
        warehouse_id: 3,
        lot_id: 2,
        progress_state: "in_progress",
        include_inactive: true,
        sort_by: "completion",
        sort_dir: "asc",
      },
      page: 2,
      pageSize: 50,
    });
  });

  it("normalizes free text and resolves existing scoped options", () => {
    expect(normalizePalletNumber("  PAL   01 ")).toBe("pal 01");
    expect(exactPalletMatch([option], " pal 01 ")).toBe(option);
    expect(palletOptionSelection(option)).toEqual({
      id: 7,
      pallet_number: "PAL 01",
    });
  });

  it("changes scope keys so pickers reset on lot or warehouse changes", () => {
    expect(palletScopeKey(2, 3)).not.toBe(palletScopeKey(2, 4));
    expect(palletScopeKey(2, 3)).not.toBe(palletScopeKey(5, 3));
  });

  it("uses an explicit unassigned fallback and role controls", () => {
    expect(unassignedPalletLabel(null)).toBe("Unassigned");
    expect(unassignedPalletLabel("PAL-1")).toBe("PAL-1");
    const roles: Role[] = ["admin", "operator", "warehouse_mover", "viewer"];
    expect(roles.filter(canManagePalletBoxes)).toEqual(["admin", "operator"]);
    expect(roles.filter(canAdministerPallet)).toEqual(["admin"]);
  });

  it("builds normalized create and box-mutation payloads", () => {
    expect(palletCreatePayload(2, 3, "  PAL   9 ")).toEqual({
      lot_id: 2,
      warehouse_id: 3,
      pallet_number: "PAL 9",
    });
    expect(palletBoxMutationPayload([4, 4, 5], "  verified ")).toEqual({
      box_ids: [4, 5],
      reason: "verified",
    });
  });
});
