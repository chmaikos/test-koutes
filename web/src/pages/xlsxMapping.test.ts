import { describe, expect, it } from "vitest";
import {
  deliveryVariance,
  groupInboundItems,
  hasRequiredDiscrepancyReason,
  mapXlsxInboundRows,
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
        { lot: "PR100", pallet_number: "PAL-1", box_number: "1", contents: "Folder A" },
        { lot: "PR100", pallet_number: " pal-1 ", box_number: "001", contents: "Folder B" },
        { lot: "PR100", pallet_number: "PAL-1", box_number: "1", contents: "Folder B" },
        { lot: "PR100", pallet_number: "PAL-2", box_number: "2", contents: "Folder C" },
      ]),
    ).toEqual([
      {
        lot: "PR100",
        box_number: "001",
        pallet_number: "PAL-1",
        contents: "Folder A | Folder B",
      },
      {
        lot: "PR100",
        box_number: "002",
        pallet_number: "PAL-2",
        contents: "Folder C",
      },
    ]);
  });

  it("keeps the same box number separate when the lot differs", () => {
    expect(
      groupInboundItems([
        { lot: "PR100", pallet_number: "PAL-1", box_number: "1", contents: "A" },
        { lot: "PR200", pallet_number: "PAL-2", box_number: "1", contents: "B" },
      ]),
    ).toHaveLength(2);
  });

  it("rejects the same lot and box mapped to different pallets", () => {
    expect(() =>
      groupInboundItems([
        { lot: "PR100", pallet_number: "PAL-1", box_number: "1" },
        { lot: "PR100", pallet_number: "PAL-2", box_number: "001" },
      ]),
    ).toThrow(/different pallets/);
  });

  it("merges duplicate unassigned rows and clearly blocks assigned conflicts", () => {
    expect(
      groupInboundItems([
        { lot: "PR100", pallet_number: null, box_number: "1", contents: "A" },
        { lot: "pr100", box_number: "001", contents: "B" },
      ]),
    ).toEqual([
      {
        lot: "PR100",
        box_number: "001",
        pallet_number: null,
        contents: "A | B",
      },
    ]);
    expect(() =>
      groupInboundItems([
        { lot: "PR100", pallet_number: "PAL-1", box_number: "1" },
        { lot: "PR100", pallet_number: null, box_number: "001" },
      ]),
    ).toThrow(/Assigned vs Unassigned/);
  });

  it("rejects pallet IDs without a canonical nonblank pallet number", () => {
    expect(() =>
      groupInboundItems([
        {
          lot: "PR100",
          box_number: "1",
          pallet_number: "   ",
          pallet_id: 9,
        },
      ]),
    ).toThrow(/pallet ID without a pallet number/);
    expect(() =>
      groupInboundItems([
        {
          lot: "PR100",
          box_number: "2",
          pallet_number: "PAL-1",
          pallet_id: 0,
        },
      ]),
    ).toThrow(/invalid pallet ID/);
  });

  it("maps no pallet column and mixed blank pallet cells without dropping rows", () => {
    const selectedRows = new Set([2, 3]);
    expect(
      mapXlsxInboundRows({
        sheet: previewSheet,
        selectedRows,
        boxColumn: 0,
        lotSource: "column",
        lotColumn: 2,
        fixedLot: "",
      }),
    ).toEqual({
      error: null,
      items: [
        { lot: "PR100", box_number: "001", pallet_number: null, contents: undefined },
        { lot: "PR100", box_number: "002", pallet_number: null, contents: undefined },
      ],
    });
    const mixed = {
      ...previewSheet,
      rows: [
        previewSheet.rows[0],
        previewSheet.rows[1],
        { ...previewSheet.rows[2], cells: ["2", "", "PR100", "B"] },
      ],
    };
    expect(
      mapXlsxInboundRows({
        sheet: mixed,
        selectedRows,
        boxColumn: 0,
        palletColumn: 1,
        lotSource: "column",
        lotColumn: 2,
        fixedLot: "",
      }).items.map((item) => item.pallet_number),
    ).toEqual(["PAL-1", null]);
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
  max_columns: 4,
  rows: [
    { row_number: 1, cells: ["Αριθμός Κιβωτίου", "Παλέτα", "Παρτίδα", "Περιεχόμενα"] },
    { row_number: 2, cells: ["1", "PAL-1", "PR100", "A"] },
    { row_number: 3, cells: ["2", "PAL-2", "PR100", "B"] },
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
      pallet_number: { index: 1, header: "Παλέτα" },
      lot: { index: 2, header: "Παρτίδα" },
      contents: { index: 3, header: "Περιεχόμενα" },
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
    expect(resolved.palletColumn).toBe(1);
    expect(resolved.lotColumn).toBe(2);
  });

  it("flags stale and out-of-range columns against the current preview", () => {
    const resolved = resolveXlsxTemplate(
      template({
        column_mappings: {
          box_number: { index: 12, header: "Missing box field" },
          pallet_number: { index: 1, header: "Παλέτα" },
          lot: { index: 2, header: "Παρτίδα" },
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
        palletColumn: 1,
        lotSource: "fixed",
        fixedLot: " PR200 ",
        contentsColumn: 3,
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

  it("saves an explicit no-pallet mapping and distinguishes it from stale templates", () => {
    const input = xlsxTemplateInput({
      useCase: "box_import",
      name: "Unassigned",
      warehouseId: 1,
      shared: false,
      filename: "boxes.xlsx",
      sheet: previewSheet,
      boxColumn: 0,
      lotSource: "fixed",
      fixedLot: "PR200",
      rowStart: 2,
      includeRowsByDefault: true,
    });
    expect(input.column_mappings.pallet_number).toBeUndefined();
    expect(
      resolveXlsxTemplate(
        template({ column_mappings: input.column_mappings }),
        previewSheet,
      ),
    ).toMatchObject({ palletColumn: undefined, warnings: [] });

    const stale = resolveXlsxTemplate(
      template({
        column_mappings: {
          ...template().column_mappings,
          pallet_number: { index: 9, header: "Missing pallet" },
        },
      }),
      previewSheet,
    );
    expect(stale.palletColumn).toBeUndefined();
    expect(stale.warnings.join(" ")).toMatch(/Pallet number.*missing or ambiguous/);
  });
});
