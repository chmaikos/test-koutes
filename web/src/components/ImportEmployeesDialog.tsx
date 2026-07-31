import { useMemo, useState } from "react";
import { Upload } from "lucide-react";
import {
  useImportMappedEmployees,
  usePreviewEmployeeImport,
} from "@/api/hooks";
import type {
  EmployeeImportResult,
  Warehouse,
} from "@/api/types";
import {
  employeeImportCell,
  mappedEmployeeItem,
  type EmployeeColumnMapping,
} from "@/components/employeeImportMapping";

export function ImportEmployeesDialog({
  warehouses,
  defaultWarehouseId,
  onClose,
}: {
  warehouses: Warehouse[];
  defaultWarehouseId: number | null;
  onClose: () => void;
}) {
  const previewMutation = usePreviewEmployeeImport();
  const importMutation = useImportMappedEmployees();
  const [warehouseId, setWarehouseId] = useState<number | null>(
    defaultWarehouseId,
  );
  const [sheetName, setSheetName] = useState("");
  const [mapping, setMapping] = useState<EmployeeColumnMapping>({
    name: null,
    hours: null,
    active: null,
    excluded: null,
  });
  const [includedRows, setIncludedRows] = useState<Set<number>>(new Set());
  const [result, setResult] = useState<EmployeeImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const preview = previewMutation.data;
  const sheet =
    preview?.sheets.find((candidate) => candidate.name === sheetName) ??
    preview?.sheets[0];
  const rows = sheet?.rows ?? [];
  const maxColumns = sheet?.max_columns ?? 0;

  const selected = useMemo(
    () => rows.filter((row) => includedRows.has(row.row_number)),
    [includedRows, rows],
  );
  const mapped = useMemo(
    () => selected.map((row) => ({ row, ...mappedEmployeeItem(row, mapping) })),
    [mapping, selected],
  );
  const validItems = mapped.flatMap((row) => (row.item ? [row.item] : []));
  const invalidRows = mapped.filter((row) => row.reason);

  function selectSheet(name: string) {
    const next = preview?.sheets.find((candidate) => candidate.name === name);
    setSheetName(name);
    setIncludedRows(new Set(next?.rows.map((row) => row.row_number) ?? []));
  }

  if (result) {
    return (
      <div className="modal-backdrop" role="dialog" aria-modal="true">
        <div className="modal-sheet max-w-2xl space-y-4">
          <div>
            <h2 className="text-lg font-semibold">Employee import complete</h2>
            <p className="text-sm text-slate-500">
              {result.created.length} created · {result.updated.length} updated ·{" "}
              {result.skipped.length} skipped
            </p>
          </div>
          {result.skipped.length > 0 && (
            <div className="max-h-64 overflow-auto rounded-lg border border-slate-200">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-xs text-slate-500">
                  <tr>
                    <th className="px-3 py-2 text-left">Row</th>
                    <th className="px-3 py-2 text-left">Employee</th>
                    <th className="px-3 py-2 text-left">Reason</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {result.skipped.map((item, index) => (
                    <tr key={`${item.row}-${index}`}>
                      <td className="px-3 py-2">{item.row}</td>
                      <td className="px-3 py-2">{item.full_name || "—"}</td>
                      <td className="px-3 py-2 text-slate-600">{item.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex justify-end">
            <button type="button" className="btn-primary" onClick={onClose}>
              Done
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-sheet max-w-6xl space-y-4">
        <header>
          <h2 className="text-lg font-semibold">Import employees</h2>
          <p className="text-sm text-slate-500">
            Upload an XLSX file, map its columns, then exclude headers or extra
            rows. Existing names in the warehouse are updated.
          </p>
        </header>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="text-xs text-slate-500">Destination warehouse</span>
            <select
              className="input"
              value={warehouseId ?? ""}
              onChange={(event) =>
                setWarehouseId(event.target.value ? Number(event.target.value) : null)
              }
            >
              <option value="">Choose warehouse</option>
              {warehouses.map((warehouse) => (
                <option key={warehouse.id} value={warehouse.id}>
                  {warehouse.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">XLSX workbook</span>
            <input
              type="file"
              accept=".xlsx"
              className="input"
              onChange={async (event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                setError(null);
                try {
                  const data = await previewMutation.mutateAsync(file);
                  const first = data.sheets[0];
                  setSheetName(first?.name ?? "");
                  setIncludedRows(
                    new Set(first?.rows.map((row) => row.row_number) ?? []),
                  );
                  setMapping({
                    name: null,
                    hours: null,
                    active: null,
                    excluded: null,
                  });
                } catch (err: unknown) {
                  const detail =
                    (err as { response?: { data?: { detail?: string } } })?.response
                      ?.data?.detail ?? "Could not preview workbook";
                  setError(typeof detail === "string" ? detail : "Could not preview workbook");
                }
              }}
            />
          </label>
        </div>

        {sheet && (
          <>
            <div className="grid gap-3 sm:grid-cols-5">
              {preview && preview.sheets.length > 1 && (
                <label className="block">
                  <span className="text-xs text-slate-500">Sheet</span>
                  <select
                    className="input"
                    value={sheet.name}
                    onChange={(event) => selectSheet(event.target.value)}
                  >
                    {preview.sheets.map((candidate) => (
                      <option key={candidate.name} value={candidate.name}>
                        {candidate.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <ColumnSelect
                label="Full name *"
                value={mapping.name}
                maxColumns={maxColumns}
                onChange={(value) => setMapping((old) => ({ ...old, name: value }))}
              />
              <ColumnSelect
                label="Hours / day"
                value={mapping.hours}
                maxColumns={maxColumns}
                onChange={(value) => setMapping((old) => ({ ...old, hours: value }))}
              />
              <ColumnSelect
                label="Active"
                value={mapping.active}
                maxColumns={maxColumns}
                onChange={(value) => setMapping((old) => ({ ...old, active: value }))}
              />
              <ColumnSelect
                label="Excluded"
                value={mapping.excluded}
                maxColumns={maxColumns}
                onChange={(value) =>
                  setMapping((old) => ({ ...old, excluded: value }))
                }
              />
            </div>

            <div className="max-h-[45vh] overflow-auto rounded-lg border border-slate-200">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-2 py-2 text-left">
                      <input
                        type="checkbox"
                        checked={rows.length > 0 && selected.length === rows.length}
                        onChange={(event) =>
                          setIncludedRows(
                            event.target.checked
                              ? new Set(rows.map((row) => row.row_number))
                              : new Set(),
                          )
                        }
                        aria-label="Include all rows"
                      />
                    </th>
                    <th className="px-2 py-2 text-left">Row</th>
                    {Array.from({ length: maxColumns }, (_, index) => (
                      <th key={index} className="px-2 py-2 text-left">
                        {columnLabel(index)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map((row) => (
                    <tr key={row.row_number}>
                      <td className="px-2 py-2">
                        <input
                          type="checkbox"
                          checked={includedRows.has(row.row_number)}
                          onChange={(event) => {
                            setIncludedRows((old) => {
                              const next = new Set(old);
                              if (event.target.checked) next.add(row.row_number);
                              else next.delete(row.row_number);
                              return next;
                            });
                          }}
                          aria-label={`Include row ${row.row_number}`}
                        />
                      </td>
                      <td className="px-2 py-2 text-slate-400">{row.row_number}</td>
                      {Array.from({ length: maxColumns }, (_, index) => (
                        <td key={index} className="max-w-56 truncate px-2 py-2">
                          {row.cells[index] ?? ""}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-slate-500">
              {selected.length} rows selected · {validItems.length} valid
              {invalidRows.length > 0 && ` · ${invalidRows.length} will be skipped`}
            </p>
          </>
        )}

        {error && <p className="text-sm text-rose-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            className="btn-secondary"
            disabled={importMutation.isPending}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={
              importMutation.isPending ||
              !warehouseId ||
              mapping.name === null ||
              validItems.length === 0
            }
            onClick={async () => {
              if (!warehouseId) return;
              setError(null);
              try {
                const imported = await importMutation.mutateAsync({
                  warehouse_id: warehouseId,
                  items: validItems,
                });
                setResult({
                  ...imported,
                  skipped: [
                    ...imported.skipped,
                    ...invalidRows.map(({ row, reason }) => ({
                      row: row.row_number,
                      full_name: employeeImportCell(row, mapping.name),
                      reason: reason ?? "invalid row",
                    })),
                  ],
                });
              } catch (err: unknown) {
                const detail =
                  (err as { response?: { data?: { detail?: string } } })?.response
                    ?.data?.detail ?? "Employee import failed";
                setError(typeof detail === "string" ? detail : "Employee import failed");
              }
            }}
          >
            <Upload className="h-4 w-4" />
            {importMutation.isPending ? "Importing..." : "Import employees"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ColumnSelect({
  label,
  value,
  maxColumns,
  onChange,
}: {
  label: string;
  value: number | null;
  maxColumns: number;
  onChange: (value: number | null) => void;
}) {
  return (
    <label className="block">
      <span className="text-xs text-slate-500">{label}</span>
      <select
        className="input"
        value={value ?? ""}
        onChange={(event) =>
          onChange(event.target.value === "" ? null : Number(event.target.value))
        }
      >
        <option value="">Not mapped</option>
        {Array.from({ length: maxColumns }, (_, index) => (
          <option key={index} value={index}>
            {columnLabel(index)}
          </option>
        ))}
      </select>
    </label>
  );
}

function columnLabel(index: number): string {
  let result = "";
  let value = index + 1;
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
}
