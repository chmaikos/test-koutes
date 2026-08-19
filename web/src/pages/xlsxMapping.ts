import type {
  InboundRequestItemInput,
  XlsxColumnRef,
  XlsxLotSource,
  XlsxMappingTemplate,
  XlsxMappingTemplateInput,
  XlsxMappingUseCase,
  XlsxPreviewSheet,
} from "@/api/types";

export interface ResolvedXlsxMapping {
  boxColumn: number | undefined;
  palletColumn: number | undefined;
  lotSource: XlsxLotSource;
  lotColumn: number | undefined;
  fixedLot: string;
  contentsColumn: number | undefined;
  rowStart: number;
  includeRowsByDefault: boolean;
  selectedRows: Set<number>;
  warnings: string[];
}

export function normalizeXlsxHeader(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "")
    .toLocaleLowerCase()
    .match(/[\p{L}\p{N}]+/gu)
    ?.join(" ") ?? "";
}

export function headersBeforeRow(
  sheet: XlsxPreviewSheet,
  rowStart: number,
): string[] {
  const header = [...sheet.rows]
    .reverse()
    .find((row) => row.row_number < rowStart);
  return header?.cells ?? [];
}

export function xlsxColumnRef(
  index: number,
  headers: string[],
): XlsxColumnRef {
  return { index, header: headers[index]?.trim() ?? "" };
}

function resolveColumn(
  ref: XlsxColumnRef | undefined,
  headers: string[],
  maxColumns: number,
  label: string,
  warnings: string[],
): number | undefined {
  if (!ref) return undefined;
  const expected = normalizeXlsxHeader(ref.header);
  if (expected) {
    const matches = headers.flatMap((header, index) =>
      normalizeXlsxHeader(header) === expected ? [index] : [],
    );
    if (matches.length === 1) return matches[0];
    if (
      ref.index < maxColumns &&
      normalizeXlsxHeader(headers[ref.index] ?? "") === expected
    ) {
      return ref.index;
    }
    warnings.push(
      `${label} column “${ref.header}” is missing or ambiguous; choose it again.`,
    );
    return undefined;
  }
  if (ref.index < maxColumns) return ref.index;
  warnings.push(`${label} column is no longer present; choose it again.`);
  return undefined;
}

export function resolveXlsxTemplate(
  template: XlsxMappingTemplate,
  sheet: XlsxPreviewSheet,
): ResolvedXlsxMapping {
  const warnings: string[] = [];
  const headers = headersBeforeRow(sheet, template.row_start);
  const boxColumn = resolveColumn(
    template.column_mappings.box_number,
    headers,
    sheet.max_columns,
    "Box number",
    warnings,
  );
  const palletColumn = resolveColumn(
    template.column_mappings.pallet_number,
    headers,
    sheet.max_columns,
    "Pallet number",
    warnings,
  );
  const lotColumn =
    template.lot_source === "column"
      ? resolveColumn(
          template.column_mappings.lot,
          headers,
          sheet.max_columns,
          "Lot",
          warnings,
        )
      : undefined;
  const contentsColumn = resolveColumn(
    template.column_mappings.contents,
    headers,
    sheet.max_columns,
    "Item descriptions",
    warnings,
  );
  const selectedRows = new Set(
    template.include_rows_by_default
      ? sheet.rows
          .filter((row) => row.row_number >= template.row_start)
          .map((row) => row.row_number)
      : [],
  );
  if (selectedRows.size === 0 && template.include_rows_by_default) {
    warnings.push("The saved first data row is beyond this worksheet.");
  }
  return {
    boxColumn,
    palletColumn,
    lotSource: template.lot_source,
    lotColumn,
    fixedLot: template.fixed_lot ?? "",
    contentsColumn,
    rowStart: template.row_start,
    includeRowsByDefault: template.include_rows_by_default,
    selectedRows,
    warnings,
  };
}

export function xlsxTemplateInput(input: {
  useCase: XlsxMappingUseCase;
  name: string;
  warehouseId?: number;
  shared: boolean;
  filename: string;
  sheet: XlsxPreviewSheet;
  boxColumn: number;
  palletColumn: number;
  lotSource: XlsxLotSource;
  lotColumn?: number;
  fixedLot: string;
  contentsColumn?: number;
  rowStart: number;
  includeRowsByDefault: boolean;
}): XlsxMappingTemplateInput {
  const headers = headersBeforeRow(input.sheet, input.rowStart);
  return {
    use_case: input.useCase,
    name: input.name.trim(),
    warehouse_id: input.shared ? (input.warehouseId ?? null) : null,
    sheet_pattern: input.sheet.name,
    filename: input.filename,
    headers,
    column_mappings: {
      box_number: xlsxColumnRef(input.boxColumn, headers),
      pallet_number: xlsxColumnRef(input.palletColumn, headers),
      ...(input.lotSource === "column" && input.lotColumn !== undefined
        ? { lot: xlsxColumnRef(input.lotColumn, headers) }
        : {}),
      ...(input.contentsColumn !== undefined
        ? { contents: xlsxColumnRef(input.contentsColumn, headers) }
        : {}),
    },
    lot_source: input.lotSource,
    fixed_lot: input.lotSource === "fixed" ? input.fixedLot.trim() : null,
    row_start: input.rowStart,
    include_rows_by_default: input.includeRowsByDefault,
  };
}

function canonicalBoxNumber(value: string): string {
  const trimmed = value.trim();
  return /^\d+$/.test(trimmed) ? trimmed.padStart(3, "0") : trimmed;
}

export function groupInboundItems(
  rows: InboundRequestItemInput[],
): InboundRequestItemInput[] {
  const grouped = new Map<
    string,
    {
      lot: string;
      box_number: string;
      pallet_number: string;
      pallet_id?: number;
      contents: string[];
    }
  >();

  for (const row of rows) {
    const lot = row.lot.trim();
    const boxNumber = canonicalBoxNumber(row.box_number);
    const key = `${lot}\u0000${boxNumber}`;
    const palletNumber = normalizePalletNumber(row.pallet_number);
    let group = grouped.get(key);
    if (!group) {
      group = {
        lot,
        box_number: boxNumber,
        pallet_number: palletNumber,
        pallet_id: row.pallet_id,
        contents: [],
      };
      grouped.set(key, group);
    } else if (normalizePalletNumber(group.pallet_number) !== palletNumber) {
      throw new Error(
        `Box ${boxNumber} in lot ${lot} is mapped to different pallets (${group.pallet_number} and ${palletNumber}).`,
      );
    }
    const contents = row.contents?.trim();
    if (contents && !group.contents.includes(contents)) {
      group.contents.push(contents);
    }
  }

  return Array.from(grouped.values(), (group) => ({
    lot: group.lot,
    box_number: group.box_number,
    pallet_number: group.pallet_number,
    ...(group.pallet_id ? { pallet_id: group.pallet_id } : {}),
    contents: group.contents.join(" | ") || undefined,
  }));
}

function normalizePalletNumber(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLocaleUpperCase();
}

export function tryGroupInboundItems(rows: InboundRequestItemInput[]): {
  items: InboundRequestItemInput[];
  error: string | null;
} {
  try {
    return { items: groupInboundItems(rows), error: null };
  } catch (caught) {
    return {
      items: [],
      error: caught instanceof Error ? caught.message : "Pallet grouping failed.",
    };
  }
}

export function deliveryVariance(ordered: number, actual: number): number {
  return actual - ordered;
}

export function hasRequiredDiscrepancyReason(
  ordered: number,
  actual: number,
  reason: string,
): boolean {
  return deliveryVariance(ordered, actual) === 0 || reason.trim().length > 0;
}
