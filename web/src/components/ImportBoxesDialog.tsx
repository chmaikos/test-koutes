import { useState } from "react";
import { Upload } from "lucide-react";
import { useImportMappedBoxes, useWarehouses } from "@/api/hooks";
import type { ImportResult, InboundRequestItemInput } from "@/api/types";
import { mappedImportPayload } from "@/pages/importResults";
import { ExcelRowMapper } from "@/components/ExcelRowMapper";

export function ImportBoxesDialog({
  onClose,
  onResult,
}: {
  onClose: () => void;
  onResult: (result: ImportResult) => void;
}) {
  const warehouses = useWarehouses();
  const importBoxes = useImportMappedBoxes();
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [mappedRows, setMappedRows] = useState<InboundRequestItemInput[]>([]);
  const [restoreArchived, setRestoreArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="modal-backdrop z-30">
      <div className="modal-sheet max-w-5xl">
        <h2 className="text-lg font-semibold">Import boxes from XLSX</h2>
        <p className="mt-1 text-sm text-slate-500">
          Choose the destination warehouse, then map any workbook layout just
          like an inbound delivery. All rows start included; exclude headers or
          unrelated data. Repeated rows for the same lot and box number are
          merged into one box.
        </p>

        <form
          className="mt-4 space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setError(null);
            if (warehouseId === "") {
              setError("Choose the destination warehouse.");
              return;
            }
            if (mappedRows.length === 0) {
              setError("Map and review at least one box before importing.");
              return;
            }
            try {
              const result = await importBoxes.mutateAsync(
                mappedImportPayload(
                  Number(warehouseId),
                  mappedRows,
                  restoreArchived,
                ),
              );
              onResult(result);
              onClose();
            } catch (err: unknown) {
              const detail =
                (err as { response?: { data?: { detail?: string } } })?.response
                  ?.data?.detail ?? "Failed to import workbook";
              setError(detail);
            }
          }}
        >
          <label className="block">
            <span className="text-xs text-slate-500">
              Destination warehouse
            </span>
            <select
              required
              className="input"
              value={warehouseId}
              onChange={(e) => {
                setWarehouseId(e.target.value ? Number(e.target.value) : "");
                setError(null);
              }}
            >
              <option value="">Choose a warehouse</option>
              {warehouses.data?.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={restoreArchived}
              onChange={(event) => setRestoreArchived(event.target.checked)}
            />
            <span>
              <strong>Restore matching archived boxes.</strong> Reuse the
              existing box when its lot and number match, preserve its audit
              and request history, and link it to this new import receipt.
              Active duplicates are still skipped.
            </span>
          </label>

          {warehouseId === "" ? (
            <p className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
              Choose the destination warehouse to load private and
              warehouse-shared mapping templates.
            </p>
          ) : (
            <ExcelRowMapper
              key={warehouseId}
              useCase="box_import"
              warehouseId={warehouseId}
              onApply={(rows) => {
                setMappedRows(rows);
                setError(null);
              }}
            />
          )}

          {mappedRows.length > 0 && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
              <p className="text-sm font-medium text-emerald-900">
                {mappedRows.length} unique box
                {mappedRows.length === 1 ? "" : "es"} ready to import
              </p>
              <div className="mt-2 max-h-32 overflow-auto text-xs text-emerald-900">
                {mappedRows.map((row) => (
                  <div key={`${row.lot}-${row.box_number}`}>
                    {row.lot} · {row.box_number}
                    {row.contents ? ` · ${row.contents}` : ""}
                  </div>
                ))}
              </div>
            </div>
          )}

          {error && <p className="text-sm text-rose-600">{error}</p>}

          <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              className="btn-secondary"
              onClick={onClose}
              disabled={importBoxes.isPending}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={
                importBoxes.isPending ||
                warehouseId === "" ||
                mappedRows.length === 0
              }
            >
              <Upload className="h-4 w-4" />
              {importBoxes.isPending
                ? "Importing..."
                : mappedRows.length > 0
                  ? `Import ${mappedRows.length} box${
                      mappedRows.length === 1 ? "" : "es"
                    }`
                  : "Import boxes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
