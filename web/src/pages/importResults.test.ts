import { describe, expect, it } from "vitest";
import type { ImportResult } from "@/api/types";
import {
  importResultTitle,
  mappedImportPayload,
} from "@/pages/importResults";

describe("archived box import options", () => {
  it("sends restoration only when the user opts in", () => {
    const items = [
      { box_number: "001", lot: "LOT-1", pallet_number: "PALLET-1" },
    ];
    expect(mappedImportPayload(2, items, false)).toEqual({
      warehouse_id: 2,
      items,
      restore_archived: false,
    });
    expect(mappedImportPayload(2, items, true).restore_archived).toBe(true);
  });

  it("reports created and restored boxes separately", () => {
    const result = {
      created: Array.from({ length: 2 }),
      restored: Array.from({ length: 1 }),
      skipped: [],
      receipt_request_ids: [10],
    } as unknown as ImportResult;
    expect(importResultTitle(result)).toBe(
      "Import result · 2 created · 1 restored · 1 receipt batch",
    );
  });
});
