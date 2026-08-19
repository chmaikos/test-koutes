import { describe, expect, it } from "vitest";
import type { PalletOption, Role } from "@/api/types";
import hooksSource from "@/api/hooks.ts?raw";
import typesSource from "@/api/types.ts?raw";
import palletDetailSource from "@/pages/PalletDetailPage.tsx?raw";
import {
  canAdministerPallet,
  canManagePalletBoxes,
  exactPalletMatch,
  normalizePalletNumber,
  palletCandidateFilters,
  palletOptionSelection,
  palletCreatePayload,
  palletBoxMutationPayload,
  palletPickerOptionFilters,
  palletScopeKey,
  palletWarehouseDistributionLabel,
  parsePalletSearchParams,
  unassignedPalletLabel,
} from "@/pages/pallets";

const option: PalletOption = {
  id: 7,
  pallet_number: "PAL 01",
  normalized_pallet_number: "pal 01",
  lot_id: 2,
  lot_name: "LOT-2",
  warehouse_ids: [3, 4],
  warehouse_names: ["North", "South"],
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

  it("scopes picker identity by lot and preserves it across warehouse changes", () => {
    expect(palletScopeKey(2)).toBe(palletScopeKey(2));
    expect(palletScopeKey(2)).not.toBe(palletScopeKey(5));
    expect(palletPickerOptionFilters(2, " PAL ", false)).toEqual({
      lot_id: 2,
      search: " PAL ",
      include_inactive: false,
    });
  });

  it("uses an explicit unassigned fallback and role controls", () => {
    expect(unassignedPalletLabel(null)).toBe("Unassigned");
    expect(unassignedPalletLabel("PAL-1")).toBe("PAL-1");
    const roles: Role[] = ["admin", "operator", "warehouse_mover", "viewer"];
    expect(roles.filter(canManagePalletBoxes)).toEqual(["admin", "operator"]);
    expect(roles.filter(canAdministerPallet)).toEqual(["admin"]);
  });

  it("labels visible box warehouse distributions without pallet ownership", () => {
    expect(palletWarehouseDistributionLabel(["North", "South"])).toBe(
      "Boxes currently in North, South",
    );
    expect(palletWarehouseDistributionLabel(["North"])).toBe(
      "Boxes currently in North",
    );
    expect(palletWarehouseDistributionLabel([])).toBe(
      "No boxes in accessible warehouses",
    );
  });

  it("shows same-lot assignment candidates across warehouses with an optional filter", () => {
    expect(palletCandidateFilters(2)).toMatchObject({
      lot_id: 2,
      warehouse_id: undefined,
      pallet_id: undefined,
    });
    expect(palletCandidateFilters(2, 4, 7)).toMatchObject({
      lot_id: 2,
      warehouse_id: 4,
      pallet_id: 7,
    });
  });

  it("keeps retired pallet move contracts and controls out of the frontend", () => {
    expect(hooksSource).not.toContain("useMovePallet");
    expect(hooksSource).not.toContain("/pallets/${input.id}/move");
    expect(typesSource).not.toContain("PalletMovePayload");
    expect(typesSource).not.toContain("PalletMoveResult");
    expect(palletDetailSource).not.toContain("Move pallet");
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
