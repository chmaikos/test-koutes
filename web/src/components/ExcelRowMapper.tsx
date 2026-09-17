import { useState } from "react";
import { Save, Share2, Trash2, Upload } from "lucide-react";
import {
  useCreateXlsxMappingTemplate,
  useDeleteXlsxMappingTemplate,
  usePreviewBoxImportXlsx,
  usePreviewInboundXlsx,
  useRecordXlsxMappingTemplateUse,
  useSuggestXlsxMappingTemplates,
  useUpdateXlsxMappingTemplate,
  useXlsxMappingTemplates,
} from "@/api/hooks";
import type {
  InboundRequestItemInput,
  XlsxMappingSuggestion,
  XlsxMappingTemplate,
  XlsxMappingUseCase,
  XlsxPreview,
  XlsxPreviewSheet,
} from "@/api/types";
import {
  deliveryVariance,
  mapXlsxInboundRows,
  resolveXlsxTemplate,
  tryGroupInboundItems,
  xlsxTemplateInput,
} from "@/pages/xlsxMapping";
import { LotPicker, type LotSelection } from "@/components/LotPicker";

export function ExcelRowMapper({
  useCase,
  warehouseId,
  requestId,
  quantity,
  onApply,
}: {
  useCase: XlsxMappingUseCase;
  warehouseId: number;
  requestId?: number;
  quantity?: number;
  onApply: (rows: InboundRequestItemInput[]) => void;
}) {
  const requestPreview = usePreviewInboundXlsx();
  const boxImportPreview = usePreviewBoxImportXlsx();
  const preview = useCase === "box_import" ? boxImportPreview : requestPreview;
  const templates = useXlsxMappingTemplates(useCase, warehouseId);
  const suggestTemplates = useSuggestXlsxMappingTemplates();
  const createTemplate = useCreateXlsxMappingTemplate();
  const updateTemplate = useUpdateXlsxMappingTemplate();
  const deleteTemplate = useDeleteXlsxMappingTemplate();
  const recordTemplateUse = useRecordXlsxMappingTemplateUse();

  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState("");
  const [boxColumn, setBoxColumn] = useState<number | undefined>();
  const [palletColumn, setPalletColumn] = useState<number | undefined>();
  const [palletMappingChoice, setPalletMappingChoice] = useState<
    "unselected" | "none" | "column"
  >("unselected");
  const [lotSource, setLotSource] = useState<"fixed" | "column">("fixed");
  const [lotColumn, setLotColumn] = useState<number | undefined>();
  const [fixedLot, setFixedLot] = useState("");
  const [fixedLotSelection, setFixedLotSelection] =
    useState<LotSelection | null>(null);
  const [fileReferenceColumn, setFileReferenceColumn] =
    useState<number | undefined>();
  const [fileDescriptionColumn, setFileDescriptionColumn] =
    useState<number | undefined>();
  const [barcodeColumn, setBarcodeColumn] = useState<number | undefined>();
  const [contentsColumn, setContentsColumn] = useState<number | undefined>();
  const [selectedRows, setSelectedRows] = useState<Set<number>>(new Set());
  const [rowStart, setRowStart] = useState(1);
  const [includeRowsByDefault, setIncludeRowsByDefault] = useState(true);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [activeTemplate, setActiveTemplate] =
    useState<XlsxMappingTemplate | null>(null);
  const [templateName, setTemplateName] = useState("");
  const [shareTemplate, setShareTemplate] = useState(false);
  const [mappingWarnings, setMappingWarnings] = useState<string[]>([]);
  const [templateMessage, setTemplateMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sheet = preview.data?.sheets.find(
    (candidate) => candidate.name === sheetName,
  );
  const suggestions = suggestTemplates.data ?? [];
  const orderedTemplates = mergeTemplateOptions(
    suggestions,
    templates.data ?? [],
  );
  const selectedTemplate = orderedTemplates.find(
    (template) => String(template.id) === selectedTemplateId,
  );
  const selectedSuggestion = suggestions.find(
    (suggestion) => String(suggestion.template.id) === selectedTemplateId,
  );

  const selectedMappedRows: InboundRequestItemInput[] = sheet
    ? sheet.rows
        .filter((row) => selectedRows.has(row.row_number))
        .map((row) => ({
          box_number:
            boxColumn === undefined ? "" : (row.cells[boxColumn] ?? "").trim(),
          pallet_number:
            palletMappingChoice !== "column" || palletColumn === undefined
              ? null
              : (row.cells[palletColumn] ?? "").trim(),
          lot:
            lotSource === "fixed"
              ? fixedLot.trim()
              : lotColumn === undefined
                ? ""
                : (row.cells[lotColumn] ?? "").trim(),
          contents:
            contentsColumn === undefined
              ? undefined
              : (row.cells[contentsColumn] ?? "").trim() || undefined,
          files:
            fileReferenceColumn === undefined
              ? undefined
              : (row.cells[fileReferenceColumn] ?? "").trim()
                ? [
                    {
                      reference: (row.cells[fileReferenceColumn] ?? "").trim(),
                      description:
                        fileDescriptionColumn === undefined
                          ? undefined
                          : (row.cells[fileDescriptionColumn] ?? "").trim() ||
                            undefined,
                      barcode:
                        barcodeColumn === undefined
                          ? undefined
                          : (row.cells[barcodeColumn] ?? "").trim() || undefined,
                    },
                  ]
                : [],
        }))
    : [];
  const selectedGrouping = tryGroupInboundItems(
    selectedMappedRows.filter(
      (row) => row.box_number && row.lot,
    ),
  );
  const groupedSelectionCount = selectedGrouping.items.length;

  function resetMapping(nextSheet: XlsxPreviewSheet | undefined) {
    setSheetName(nextSheet?.name ?? "");
    setBoxColumn(undefined);
    setPalletColumn(undefined);
    setPalletMappingChoice("unselected");
    setLotSource("fixed");
    setLotColumn(undefined);
    setFixedLot("");
    setFixedLotSelection(null);
    setFileReferenceColumn(undefined);
    setFileDescriptionColumn(undefined);
    setBarcodeColumn(undefined);
    setContentsColumn(undefined);
    setRowStart(1);
    setIncludeRowsByDefault(true);
    setSelectedRows(
      new Set(nextSheet?.rows.map((row) => row.row_number) ?? []),
    );
    setSelectedTemplateId("");
    setActiveTemplate(null);
    setTemplateName("");
    setShareTemplate(false);
    setMappingWarnings([]);
    setTemplateMessage(null);
    setError(null);
  }

  async function requestSuggestions(
    result: XlsxPreview,
    nextSheet: XlsxPreviewSheet,
  ) {
    try {
      const matches = await suggestTemplates.mutateAsync({
        use_case: useCase,
        warehouse_id: warehouseId,
        filename: result.filename,
        sheet_name: nextSheet.name,
        header_candidates: nextSheet.rows.slice(0, 20).map((row) => row.cells),
      });
      setSelectedTemplateId(
        matches[0] ? String(matches[0].template.id) : "",
      );
    } catch {
      // A suggestion failure must not block manual mapping.
    }
  }

  async function loadWorkbook() {
    if (!file) return;
    setError(null);
    try {
      const result =
        useCase === "box_import"
          ? await boxImportPreview.mutateAsync({ file })
          : await requestPreview.mutateAsync({ requestId: requestId!, file });
      const firstSheet = result.sheets[0];
      resetMapping(firstSheet);
      if (firstSheet) {
        await requestSuggestions(result, firstSheet);
      }
    } catch (caught) {
      setError(apiError(caught, "Could not read the workbook."));
    }
  }

  function toggleRow(rowNumber: number) {
    setSelectedRows((current) => {
      const next = new Set(current);
      if (next.has(rowNumber)) next.delete(rowNumber);
      else next.add(rowNumber);
      return next;
    });
  }

  function applySelectedTemplate() {
    if (!sheet || !selectedTemplate) return;
    const resolved = resolveXlsxTemplate(selectedTemplate, sheet);
    setBoxColumn(resolved.boxColumn);
    setPalletColumn(resolved.palletColumn);
    setPalletMappingChoice(
      selectedTemplate.column_mappings.pallet_number
        ? resolved.palletColumn === undefined
          ? "unselected"
          : "column"
        : "none",
    );
    setLotSource(resolved.lotSource);
    setLotColumn(resolved.lotColumn);
    setFixedLot(resolved.fixedLot);
    setFixedLotSelection(null);
    setFileReferenceColumn(resolved.fileReferenceColumn);
    setFileDescriptionColumn(resolved.fileDescriptionColumn);
    setBarcodeColumn(resolved.barcodeColumn);
    setContentsColumn(resolved.contentsColumn);
    setRowStart(resolved.rowStart);
    setIncludeRowsByDefault(resolved.includeRowsByDefault);
    setSelectedRows(resolved.selectedRows);
    setActiveTemplate(selectedTemplate);
    setTemplateName(selectedTemplate.name);
    setShareTemplate(selectedTemplate.is_shared);
    setMappingWarnings(resolved.warnings);
    setTemplateMessage(
      `Applied “${selectedTemplate.name}” to this preview. Review the columns and included rows, then confirm below.`,
    );
    setError(null);
  }

  function currentTemplatePayload(name: string) {
    if (!sheet || boxColumn === undefined) {
      throw new Error("Choose the box number column before saving a template.");
    }
    if (palletMappingChoice === "unselected") {
      throw new Error(
        "Choose a pallet column or explicitly choose to leave new boxes Unassigned.",
      );
    }
    if (lotSource === "fixed" && !fixedLot.trim()) {
      throw new Error("Enter the fixed lot before saving a template.");
    }
    if (lotSource === "column" && lotColumn === undefined) {
      throw new Error("Choose the lot column before saving a template.");
    }
    if (fileReferenceColumn === undefined) {
      throw new Error("Choose the file reference column before saving a template.");
    }
    return xlsxTemplateInput({
      useCase,
      name,
      warehouseId,
      shared: shareTemplate,
      filename: preview.data?.filename ?? file?.name ?? "",
      sheet,
      boxColumn,
      palletColumn:
        palletMappingChoice === "column" ? palletColumn : undefined,
      lotSource,
      lotColumn,
      fixedLot,
      fileReferenceColumn,
      fileDescriptionColumn,
      barcodeColumn,
      contentsColumn,
      rowStart,
      includeRowsByDefault,
    });
  }

  async function saveNewTemplate() {
    setError(null);
    setTemplateMessage(null);
    if (!templateName.trim()) {
      setError("Enter a template name.");
      return;
    }
    try {
      const created = await createTemplate.mutateAsync(
        currentTemplatePayload(templateName),
      );
      setSelectedTemplateId(String(created.id));
      setActiveTemplate(created);
      setTemplateName(created.name);
      setShareTemplate(created.is_shared);
      setTemplateMessage(
        `Saved “${created.name}”. It will only be applied after you choose and confirm it.`,
      );
    } catch (caught) {
      setError(apiError(caught, "Could not save the mapping template."));
    }
  }

  async function updateCurrentTemplate() {
    const target = selectedTemplate ?? activeTemplate;
    if (!target?.is_owner) return;
    setError(null);
    setTemplateMessage(null);
    try {
      const current = currentTemplatePayload(templateName || target.name);
      const updated = await updateTemplate.mutateAsync({
        templateId: target.id,
        payload: {
          name: current.name,
          warehouse_id: current.warehouse_id,
          sheet_pattern: current.sheet_pattern,
          filename: current.filename,
          headers: current.headers,
          column_mappings: current.column_mappings,
          lot_source: current.lot_source,
          fixed_lot: current.fixed_lot,
          row_start: current.row_start,
          include_rows_by_default: current.include_rows_by_default,
        },
      });
      setActiveTemplate(updated);
      setSelectedTemplateId(String(updated.id));
      setTemplateMessage(`Updated “${updated.name}”.`);
    } catch (caught) {
      setError(apiError(caught, "Could not update the mapping template."));
    }
  }

  async function removeCurrentTemplate() {
    const target = selectedTemplate ?? activeTemplate;
    if (
      !target?.is_owner ||
      !window.confirm(`Delete mapping template “${target.name}”?`)
    ) {
      return;
    }
    setError(null);
    try {
      await deleteTemplate.mutateAsync(target.id);
      setSelectedTemplateId("");
      setActiveTemplate(null);
      setTemplateName("");
      setShareTemplate(false);
      setMappingWarnings([]);
      setTemplateMessage(`Deleted “${target.name}”.`);
    } catch (caught) {
      setError(apiError(caught, "Could not delete the mapping template."));
    }
  }

  function applyMapping() {
    if (!sheet || boxColumn === undefined) {
      setError("Choose the column containing the box number.");
      return;
    }
    if (
      palletMappingChoice === "unselected" ||
      (palletMappingChoice === "column" && palletColumn === undefined)
    ) {
      setError(
        "Choose a pallet column or explicitly choose to leave new boxes Unassigned.",
      );
      return;
    }
    if (lotSource === "fixed" && !fixedLot.trim()) {
      setError("Enter the lot value to apply to the selected rows.");
      return;
    }
    if (lotSource === "fixed" && !fixedLotSelection) {
      setError(
        "Select the existing fixed lot or explicitly create and confirm a new one.",
      );
      return;
    }
    if (lotSource === "column" && lotColumn === undefined) {
      setError("Choose the column containing the lot.");
      return;
    }
    if (fileReferenceColumn === undefined) {
      setError("Choose the column containing the required file reference.");
      return;
    }
    const selectedColumns = [
      boxColumn,
      ...(lotSource === "column" && lotColumn != null ? [lotColumn] : []),
      ...(contentsColumn == null ? [] : [contentsColumn]),
      fileReferenceColumn,
      ...(fileDescriptionColumn == null ? [] : [fileDescriptionColumn]),
      ...(barcodeColumn == null ? [] : [barcodeColumn]),
      ...(palletMappingChoice === "column" && palletColumn != null
        ? [palletColumn]
        : []),
    ];
    if (new Set(selectedColumns).size !== selectedColumns.length) {
      setError("Each mapped field must use a different column.");
      return;
    }
    if (selectedRows.size === 0) {
      setError("Select at least one workbook row.");
      return;
    }

    const groupedResult = mapXlsxInboundRows({
      sheet,
      selectedRows,
      boxColumn,
      palletColumn:
        palletMappingChoice === "column" ? palletColumn : undefined,
      lotSource,
      lotColumn,
      fixedLot,
      fileReferenceColumn,
      fileDescriptionColumn,
      barcodeColumn,
      contentsColumn,
    });
    if (groupedResult.error) {
      setError(groupedResult.error);
      return;
    }
    const grouped = groupedResult.items;
    const oversized = grouped.find((item) => (item.contents?.length ?? 0) > 2000);
    if (oversized) {
      setError(
        `Combined item descriptions for box ${oversized.box_number} exceed 2,000 characters.`,
      );
      return;
    }
    setError(null);
    if (activeTemplate) {
      recordTemplateUse.mutate(activeTemplate.id);
    }
    onApply(grouped);
  }

  return (
    <div className="mt-4 space-y-4 border-t border-slate-200 pt-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="block flex-1">
          <span className="text-xs text-slate-500">Excel workbook</span>
          <input
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="input file:mr-3 file:border-0 file:bg-transparent file:text-sm"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setError(null);
            }}
          />
        </label>
        <button
          type="button"
          className="btn-secondary"
          disabled={!file || preview.isPending}
          onClick={() => void loadWorkbook()}
        >
          <Upload className="h-4 w-4" />
          {preview.isPending ? "Reading…" : "Preview workbook"}
        </button>
      </div>

      {preview.data && sheet && (
        <>
          <section className="space-y-3 rounded-lg border border-brand-200 bg-brand-50/60 p-3">
            <div>
              <h4 className="text-sm font-medium text-slate-800">
                Saved mapping templates
              </h4>
              <p className="text-xs text-slate-600">
                Suggestions are never applied automatically. Choose a template,
                inspect its match details, then apply it to this preview.
              </p>
            </div>
            <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]">
              <label className="block">
                <span className="text-xs text-slate-500">Template</span>
                <select
                  className="input"
                  value={selectedTemplateId}
                  onChange={(event) => {
                    const next = orderedTemplates.find(
                      (template) => String(template.id) === event.target.value,
                    );
                    setSelectedTemplateId(event.target.value);
                    setTemplateName(next?.name ?? "");
                    setShareTemplate(next?.is_shared ?? false);
                    setActiveTemplate(null);
                    setMappingWarnings([]);
                    setTemplateMessage(null);
                  }}
                >
                  <option value="">Map manually</option>
                  {orderedTemplates.map((template) => {
                    const suggestion = suggestions.find(
                      (item) => item.template.id === template.id,
                    );
                    return (
                      <option key={template.id} value={template.id}>
                        {suggestion
                          ? `${Math.round(suggestion.confidence * 100)}% · `
                          : ""}
                        {template.name}
                        {template.is_shared
                          ? ` · shared by ${template.owner_name}`
                          : " · private"}
                      </option>
                    );
                  })}
                </select>
              </label>
              <button
                type="button"
                className="btn-primary self-end"
                disabled={!selectedTemplate}
                onClick={applySelectedTemplate}
              >
                Apply selected template
              </button>
            </div>
            {selectedSuggestion && (
              <p className="text-xs text-brand-900">
                <strong>
                  {Math.round(selectedSuggestion.confidence * 100)}% confidence:
                </strong>{" "}
                {selectedSuggestion.explanation}.
              </p>
            )}
            {suggestTemplates.isPending && (
              <p className="text-xs text-slate-500">
                Ranking saved templates…
              </p>
            )}

            <div className="grid gap-2 border-t border-brand-200 pt-3 md:grid-cols-[minmax(0,1fr)_auto_auto_auto]">
              <label className="block">
                <span className="text-xs text-slate-500">Template name</span>
                <input
                  className="input"
                  maxLength={120}
                  placeholder="For example, Weekly inbound layout"
                  value={templateName}
                  onChange={(event) => setTemplateName(event.target.value)}
                />
              </label>
              <label className="flex items-center gap-2 self-end rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
                <input
                  type="checkbox"
                  checked={shareTemplate}
                  onChange={(event) => setShareTemplate(event.target.checked)}
                />
                <Share2 className="h-4 w-4" />
                Share with this warehouse
              </label>
              <button
                type="button"
                className="btn-secondary self-end"
                disabled={
                  !templateName.trim() ||
                  createTemplate.isPending ||
                  updateTemplate.isPending
                }
                onClick={() => void saveNewTemplate()}
              >
                <Save className="h-4 w-4" /> Save new
              </button>
              <div className="flex self-end">
                <button
                  type="button"
                  className="btn-secondary rounded-r-none"
                  disabled={
                    !(selectedTemplate ?? activeTemplate)?.is_owner ||
                    updateTemplate.isPending
                  }
                  onClick={() => void updateCurrentTemplate()}
                >
                  Update
                </button>
                <button
                  type="button"
                  className="btn-secondary rounded-l-none border-l-0 text-rose-600"
                  aria-label="Delete selected template"
                  disabled={
                    !(selectedTemplate ?? activeTemplate)?.is_owner ||
                    deleteTemplate.isPending
                  }
                  onClick={() => void removeCurrentTemplate()}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
            {selectedTemplate && !selectedTemplate.is_owner && (
              <p className="text-xs text-slate-600">
                Warehouse-shared templates can only be changed or deleted by
                their owner. Save a private copy to customize this mapping.
              </p>
            )}
            {templateMessage && (
              <p className="text-xs text-emerald-700">{templateMessage}</p>
            )}
            {mappingWarnings.length > 0 && (
              <div
                role="alert"
                className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900"
              >
                <strong>This saved mapping is stale:</strong>
                <ul className="mt-1 list-disc pl-5">
                  {mappingWarnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
                Re-select the missing columns before confirming.
              </div>
            )}
          </section>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <label className="block">
              <span className="text-xs text-slate-500">Worksheet</span>
              <select
                className="input"
                value={sheetName}
                onChange={(event) => {
                  const nextSheet = preview.data?.sheets.find(
                    (candidate) => candidate.name === event.target.value,
                  );
                  resetMapping(nextSheet);
                  if (preview.data && nextSheet) {
                    void requestSuggestions(preview.data, nextSheet);
                  }
                }}
              >
                {preview.data.sheets.map((candidate) => (
                  <option key={candidate.name} value={candidate.name}>
                    {candidate.name}
                  </option>
                ))}
              </select>
            </label>
            <ColumnSelect
              label="Box number column"
              sheet={sheet}
              value={boxColumn}
              required
              onChange={setBoxColumn}
            />
            <PalletColumnSelect
              sheet={sheet}
              value={palletColumn}
              choice={palletMappingChoice}
              onChange={(choice, column) => {
                setPalletMappingChoice(choice);
                setPalletColumn(column);
                if (choice !== "unselected") {
                  setMappingWarnings((warnings) =>
                    warnings.filter(
                      (warning) => !warning.startsWith("Pallet number column"),
                    ),
                  );
                }
              }}
            />
            <label className="block">
              <span className="text-xs text-slate-500">Lot source</span>
              <select
                className="input"
                value={lotSource}
                onChange={(event) =>
                  setLotSource(event.target.value as "fixed" | "column")
                }
              >
                <option value="fixed">One fixed lot value</option>
                <option value="column">Workbook column</option>
              </select>
            </label>
            {lotSource === "fixed" ? (
              <LotPicker
                label="Fixed lot"
                value={fixedLotSelection}
                nameValue={fixedLot}
                onNameChange={setFixedLot}
                onChange={setFixedLotSelection}
                warehouseId={warehouseId}
                canCreate
              />
            ) : (
              <ColumnSelect
                label="Lot column"
                sheet={sheet}
                value={lotColumn}
                required
                onChange={setLotColumn}
              />
            )}
            <ColumnSelect
              label="File reference column"
              sheet={sheet}
              value={fileReferenceColumn}
              required
              onChange={setFileReferenceColumn}
            />
            <ColumnSelect
              label="File description column (optional)"
              sheet={sheet}
              value={fileDescriptionColumn}
              onChange={setFileDescriptionColumn}
            />
            <ColumnSelect
              label="File barcode column (optional)"
              sheet={sheet}
              value={barcodeColumn}
              onChange={setBarcodeColumn}
            />
            <ColumnSelect
              label="Legacy contents column (optional)"
              sheet={sheet}
              value={contentsColumn}
              onChange={setContentsColumn}
            />
            <label className="block">
              <span className="text-xs text-slate-500">First data row</span>
              <input
                type="number"
                className="input"
                min={1}
                max={5000}
                value={rowStart}
                onChange={(event) => {
                  const next = Math.min(
                    5000,
                    Math.max(1, Number(event.target.value) || 1),
                  );
                  setRowStart(next);
                  if (includeRowsByDefault) {
                    setSelectedRows(
                      new Set(
                        sheet.rows
                          .filter((row) => row.row_number >= next)
                          .map((row) => row.row_number),
                      ),
                    );
                  }
                }}
              />
            </label>
            <label className="flex items-center gap-2 self-end rounded-lg border border-slate-200 px-3 py-2 text-sm">
              <input
                type="checkbox"
                checked={includeRowsByDefault}
                onChange={(event) =>
                  setIncludeRowsByDefault(event.target.checked)
                }
              />
              Include rows by default when applied
            </label>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600">
            <span>
              All rows are included initially. Uncheck headers and extra data
              to exclude them. Repeated box numbers are merged.
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="text-brand-700 underline hover:text-brand-800"
                onClick={() =>
                  setSelectedRows(
                    new Set(sheet.rows.map((row) => row.row_number)),
                  )
                }
              >
                Include all
              </button>
              <button
                type="button"
                className="text-slate-600 underline hover:text-slate-800"
                onClick={() => setSelectedRows(new Set())}
              >
                Exclude all
              </button>
              <span className="font-medium tabular-nums">
                {selectedRows.size} rows → {groupedSelectionCount} unique boxes
              </span>
            </div>
          </div>
          {selectedGrouping.error && (
            <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">
              {selectedGrouping.error}
            </p>
          )}
          {quantity !== undefined && (
            <DeliveryVarianceSummary
              ordered={quantity}
              actual={groupedSelectionCount}
            />
          )}

          <div className="max-h-80 overflow-auto rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full border-collapse text-xs">
              <thead className="sticky top-0 z-10 bg-slate-100 text-left text-slate-600">
                <tr>
                  <th className="w-16 border-b px-2 py-2">Included</th>
                  <th className="w-14 border-b px-2 py-2">Row</th>
                  {Array.from({ length: sheet.max_columns }, (_, index) => (
                    <th
                      key={index}
                      className="min-w-32 border-b border-l px-2 py-2"
                    >
                      Column {excelColumnName(index)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sheet.rows.map((row) => {
                  const checked = selectedRows.has(row.row_number);
                  return (
                    <tr
                      key={row.row_number}
                      className={checked ? "bg-brand-50" : "hover:bg-slate-50"}
                    >
                      <td className="border-b px-2 py-1.5 text-center">
                        <input
                          type="checkbox"
                          checked={checked}
                          aria-label={`Use Excel row ${row.row_number}`}
                          onChange={() => toggleRow(row.row_number)}
                        />
                      </td>
                      <td className="border-b px-2 py-1.5 font-mono text-slate-500">
                        {row.row_number}
                      </td>
                      {Array.from(
                        { length: sheet.max_columns },
                        (_, columnIndex) => (
                          <td
                            key={columnIndex}
                            className={`max-w-64 truncate border-b border-l px-2 py-1.5 ${
                              columnIndex === boxColumn ||
                              columnIndex === lotColumn ||
                              columnIndex === palletColumn ||
                              columnIndex === fileReferenceColumn ||
                              columnIndex === fileDescriptionColumn ||
                              columnIndex === barcodeColumn ||
                              columnIndex === contentsColumn
                                ? "bg-amber-50"
                                : ""
                            }`}
                            title={row.cells[columnIndex] ?? ""}
                          >
                            {row.cells[columnIndex] || "—"}
                          </td>
                        ),
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex flex-col items-end gap-1">
            <button
              type="button"
              className="btn-primary"
              disabled={
                selectedRows.size === 0 ||
                groupedSelectionCount === 0 ||
                !!selectedGrouping.error
              }
              onClick={applyMapping}
            >
              Confirm mapping and review
            </button>
            <span className="text-xs text-slate-500">
              This confirmation only fills the review form; it does not import
              or complete the request.
            </span>
          </div>
        </>
      )}

      {error && (
        <p role="alert" className="text-sm text-rose-600">
          {error}
        </p>
      )}
    </div>
  );
}

function mergeTemplateOptions(
  suggestions: XlsxMappingSuggestion[],
  templates: XlsxMappingTemplate[],
): XlsxMappingTemplate[] {
  const merged = new Map<number, XlsxMappingTemplate>();
  for (const suggestion of suggestions) {
    merged.set(suggestion.template.id, suggestion.template);
  }
  for (const template of templates) {
    if (!merged.has(template.id)) merged.set(template.id, template);
  }
  return [...merged.values()];
}

function PalletColumnSelect({
  sheet,
  value,
  choice,
  onChange,
}: {
  sheet: XlsxPreviewSheet;
  value: number | undefined;
  choice: "unselected" | "none" | "column";
  onChange: (
    choice: "unselected" | "none" | "column",
    value: number | undefined,
  ) => void;
}) {
  const selectValue =
    choice === "none" ? "none" : choice === "column" ? String(value) : "";
  return (
    <label className="block">
      <span className="text-xs text-slate-500">
        Pallet number column (optional)
      </span>
      <select
        className="input"
        value={selectValue}
        onChange={(event) => {
          if (event.target.value === "") {
            onChange("unselected", undefined);
          } else if (event.target.value === "none") {
            onChange("none", undefined);
          } else {
            onChange("column", Number(event.target.value));
          }
        }}
      >
        <option value="">Choose a column or Unassigned</option>
        <option value="none">
          Do not import pallet / leave new boxes Unassigned
        </option>
        {Array.from({ length: sheet.max_columns }, (_, index) => {
          const examples = sheet.rows
            .map((row) => row.cells[index])
            .filter(Boolean)
            .slice(0, 3)
            .join(" · ");
          return (
            <option key={index} value={index}>
              {excelColumnName(index)}
              {examples ? ` — ${examples.slice(0, 70)}` : ""}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function ColumnSelect({
  label,
  sheet,
  value,
  required = false,
  onChange,
}: {
  label: string;
  sheet: XlsxPreviewSheet;
  value: number | undefined;
  required?: boolean;
  onChange: (value: number | undefined) => void;
}) {
  return (
    <label className="block">
      <span className="text-xs text-slate-500">{label}</span>
      <select
        className="input"
        value={value ?? ""}
        onChange={(event) =>
          onChange(
            event.target.value === "" ? undefined : Number(event.target.value),
          )
        }
      >
        <option value="">{required ? "Choose a column" : "Do not import"}</option>
        {Array.from({ length: sheet.max_columns }, (_, index) => {
          const examples = sheet.rows
            .map((row) => row.cells[index])
            .filter(Boolean)
            .slice(0, 3)
            .join(" · ");
          return (
            <option key={index} value={index}>
              {excelColumnName(index)}
              {examples ? ` — ${examples.slice(0, 70)}` : ""}
            </option>
          );
        })}
      </select>
    </label>
  );
}

function DeliveryVarianceSummary({
  ordered,
  actual,
}: {
  ordered: number;
  actual: number;
}) {
  const variance = deliveryVariance(ordered, actual);
  const result =
    variance === 0
      ? "Exact delivery"
      : variance < 0
        ? `${Math.abs(variance)} short`
        : `${variance} over`;
  return (
    <div
      className={`rounded-lg border p-3 text-sm ${
        variance === 0
          ? "border-emerald-200 bg-emerald-50 text-emerald-900"
          : "border-amber-200 bg-amber-50 text-amber-900"
      }`}
    >
      <strong>{result}</strong> · ordered {ordered}, mapped {actual}
    </div>
  );
}

function excelColumnName(index: number): string {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    value -= 1;
    name = String.fromCharCode(65 + (value % 26)) + name;
    value = Math.floor(value / 26);
  }
  return name;
}

function apiError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}
