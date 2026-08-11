import { describe, expect, it } from "vitest";
import type { LotOption, LotStatusCounts, Role } from "@/api/types";
import {
  canReassignLot,
  completionLabel,
  exactLotMatch,
  lotConflictCurrent,
  lotRenamePayload,
  lotSelection,
  lotStatusSegments,
  normalizeLotName,
  parseLotSearchParams,
  reassignmentPayload,
  renameValidation,
  shouldOfferLotCreation,
} from "@/pages/lots";

const counts: LotStatusCounts = {
  quarantined: 1,
  received: 2,
  processing: 0,
  incomplete: 3,
  ready_to_return: 0,
  returned: 4,
};

describe("lot completion and status display", () => {
  it("distinguishes a zero denominator from zero percent", () => {
    expect(completionLabel(null)).toBe("No eligible boxes");
    expect(completionLabel(0)).toBe("0.0%");
    expect(completionLabel(62.345)).toBe("62.3%");
  });

  it("builds accessible non-zero status segments", () => {
    expect(lotStatusSegments(counts)).toEqual([
      { status: "quarantined", count: 1, percent: 10 },
      { status: "received", count: 2, percent: 20 },
      { status: "incomplete", count: 3, percent: 30 },
      { status: "returned", count: 4, percent: 40 },
    ]);
  });
});

describe("lot URL filters", () => {
  it("parses valid filters, sorts, and pagination", () => {
    const result = parseLotSearchParams(
      new URLSearchParams(
        "q=Alpha&warehouse_id=3&progress=in_progress&sort_by=completion&sort_dir=asc&page=2&page_size=50",
      ),
    );
    expect(result).toEqual({
      filters: {
        search: "Alpha",
        warehouse_id: 3,
        progress_state: "in_progress",
        sort_by: "completion",
        sort_dir: "asc",
      },
      page: 2,
      pageSize: 50,
    });
  });

  it("drops invalid URL values", () => {
    expect(
      parseLotSearchParams(
        new URLSearchParams(
          "warehouse_id=-2&progress=done&sort_by=owner&page=-4&page_size=999",
        ),
      ),
    ).toEqual({ filters: {}, page: 1, pageSize: 25 });
  });
});

describe("lot picker identity", () => {
  const options: LotOption[] = [
    {
      id: 7,
      name: "Spring Intake",
      normalized_name: "spring intake",
      exact_normalized_match: true,
    },
  ];

  it("normalizes case and whitespace before duplicate checks", () => {
    expect(normalizeLotName("  SPRING\t intake ")).toBe("spring intake");
    expect(exactLotMatch(options, " SPRING   INTAKE ")?.id).toBe(7);
    expect(exactLotMatch(options, "New lot")).toBeUndefined();
    expect(lotSelection(options[0])).toEqual({
      id: 7,
      name: "Spring Intake",
    });
    expect(shouldOfferLotCreation(options, "New lot", true, 3)).toBe(true);
    expect(
      shouldOfferLotCreation(options, " SPRING INTAKE ", true, 3),
    ).toBe(false);
    expect(shouldOfferLotCreation(options, "New lot", false, 3)).toBe(false);
  });
});

describe("audited lot changes", () => {
  it("validates and builds rename payloads", () => {
    expect(renameValidation("Name", " ")).toBe(
      "A correction reason is required.",
    );
    expect(lotRenamePayload("  Corrected   Name ", " typo ", 4)).toEqual({
      new_name: "Corrected Name",
      reason: "typo",
      expected_version: 4,
    });
  });

  it("recovers latest state from a conflict response", () => {
    const current = {
      id: 1,
      name: "Latest",
      normalized_name: "latest",
      version: 3,
      updated_at: "2026-08-11T10:00:00Z",
    };
    expect(
      lotConflictCurrent({
        response: { status: 409, data: { detail: { current } } },
      }),
    ).toEqual(current);
  });

  it("gates reassignment and builds the audited payload", () => {
    const roles: Role[] = ["admin", "warehouse_mover", "operator", "viewer"];
    expect(roles.filter(canReassignLot)).toEqual(["admin"]);
    expect(reassignmentPayload(9, " verified label ", 5)).toEqual({
      lot_id: 9,
      reason: "verified label",
      expected_lot_version: 5,
    });
  });
});

