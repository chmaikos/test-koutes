import { describe, expect, it } from "vitest";
import {
  deliveryVariance,
  groupInboundItems,
  hasRequiredDiscrepancyReason,
  normalizeXlsxHeader,
  resolveXlsxTemplate,
  xlsxTemplateInput,
} from "@/pages/xlsxMapping";
import type {
  XlsxMappingTemplate,
  XlsxPreviewSheet,
} from "@/api/types";

describe("Excel inbound row grouping", () => {
  it("merges repeated box numbers and combines distinct contents", () => {
    expect(
      groupInboundItems([
        { lot: "PR100", box_number: "1", contents: "Folder A" },
        { lot: "PR100", box_number: "001", contents: "Folder B" },
        { lot: "PR100", box_number: "1", contents: "Folder B" },
        { lot: "PR100", box_number: "2", contents: "Folder C" },
      ]),
    ).toEqual([
      {
        lot: "PR100",
        box_number: "001",
        contents: "Folder A | Folder B",
      },
      {
        lot: "PR100",
        box_number: "002",
        contents: "Folder C",
      },
    ]);
  });

  it("keeps the same box number separate when the lot differs", () => {
    expect(
      groupInboundItems([
        { lot: "PR100", box_number: "1", contents: "A" },
        { lot: "PR200", box_number: "1", contents: "B" },
      ]),
    ).toHaveLength(2);
  });
});

describe("delivery count variance", () => {
  it("calculates exact, short, and over deliveries", () => {
    expect(deliveryVariance(3, 3)).toBe(0);
    expect(deliveryVariance(3, 2)).toBe(-1);
    expect(deliveryVariance(3, 5)).toBe(2);
  });

  it("requires a non-blank reason only when counts differ", () => {
    expect(hasRequiredDiscrepancyReason(3, 3, "")).toBe(true);
    expect(hasRequiredDiscrepancyReason(3, 2, "")).toBe(false);
    expect(hasRequiredDiscrepancyReason(3, 4, "   ")).toBe(false);
    expect(
      hasRequiredDiscrepancyReason(3, 4, "Accepted an extra prepared box"),
    ).toBe(true);
  });
});

const previewSheet: XlsxPreviewSheet = {
  name: "Παραλαβές Αυγούστου",
  max_columns: 3,
  rows: [
    { row_number: 1, cells: ["Αριθμός Κιβωτίου", "Παρτίδα", "Περιεχόμενα"] },
    { row_number: 2, cells: ["1", "PR100", "A"] },
    { row_number: 3, cells: ["2", "PR100", "B"] },
  ],
};

function template(
  overrides: Partial<XlsxMappingTemplate> = {},
): XlsxMappingTemplate {
  return {
    id: 1,
    owner_user_id: 10,
    owner_name: "Owner",
    warehouse_id: null,
    use_case: "inbound_acceptance",
    name: "Inbound",
    sheet_pattern: "Παραλαβές*",
    filename_fingerprint: "incoming xlsx",
    header_fingerprint: "header",
    column_mappings: {
      box_number: { index: 0, header: "Αριθμός Κιβωτίου" },
      lot: { index: 1, header: "Παρτίδα" },
      contents: { index: 2, header: "Περιεχόμενα" },
    },
    lot_source: "column",
    fixed_lot: null,
    row_start: 2,
    include_rows_by_default: true,
    usage_count: 0,
    last_used_at: null,
    created_at: "2026-08-10T00:00:00Z",
    updated_at: "2026-08-10T00:00:00Z",
    is_owner: true,
    is_shared: false,
    ...overrides,
  };
}

describe("saved Excel mapping templates", () => {
  it("preserves Unicode headers and default row inclusion", () => {
    expect(normalizeXlsxHeader("  ΑΡΙΘΜΟΣ—Κιβωτίου ")).toBe(
      "αριθμος κιβωτιου",
    );
    const resolved = resolveXlsxTemplate(template(), previewSheet);
    expect(resolved.warnings).toEqual([]);
    expect([...resolved.selectedRows]).toEqual([2, 3]);
    expect(resolved.boxColumn).toBe(0);
    expect(resolved.lotColumn).toBe(1);
  });

  it("flags stale and out-of-range columns against the current preview", () => {
    const resolved = resolveXlsxTemplate(
      template({
        column_mappings: {
          box_number: { index: 12, header: "Missing box field" },
          lot: { index: 1, header: "Παρτίδα" },
          contents: { index: 20, header: "" },
        },
      }),
      previewSheet,
    );
    expect(resolved.boxColumn).toBeUndefined();
    expect(resolved.contentsColumn).toBeUndefined();
    expect(resolved.warnings).toHaveLength(2);
  });

  it("round-trips fixed lot rules for both mapper workflows", () => {
    for (const useCase of ["box_import", "inbound_acceptance"] as const) {
      const input = xlsxTemplateInput({
        useCase,
        name: "Fixed lot",
        warehouseId: 1,
        shared: true,
        filename: "boxes.xlsx",
        sheet: previewSheet,
        boxColumn: 0,
        lotSource: "fixed",
        fixedLot: " PR200 ",
        contentsColumn: 2,
        rowStart: 2,
        includeRowsByDefault: false,
      });
      expect(input.use_case).toBe(useCase);
      expect(input.fixed_lot).toBe("PR200");
      expect(input.column_mappings.lot).toBeUndefined();
      expect(input.include_rows_by_default).toBe(false);
      expect(input).not.toHaveProperty("rows");
    }
  });
});
