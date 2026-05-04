import { useState } from "react";
import { Upload } from "lucide-react";
import { useImportBoxes, useWarehouses } from "@/api/hooks";
import type { ImportResult } from "@/api/types";

export function ImportBoxesDialog({
  onClose,
  onResult,
}: {
  onClose: () => void;
  onResult: (result: ImportResult) => void;
}) {
  const warehouses = useWarehouses();
  const importBoxes = useImportBoxes();
  const [file, setFile] = useState<File | null>(null);
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="card card-pad w-full max-w-md">
        <h2 className="text-lg font-semibold">Import boxes from XLSX</h2>
        <p className="mt-1 text-sm text-slate-500">
          Each row creates a new box in the <strong>received</strong> state.
          Recognised columns (case-insensitive):{" "}
          <code className="rounded bg-slate-100 px-1">box_number</code>,{" "}
          <code className="rounded bg-slate-100 px-1">owner</code>,{" "}
          <code className="rounded bg-slate-100 px-1">warehouse_id</code> or{" "}
          <code className="rounded bg-slate-100 px-1">warehouse</code>{" "}
          (by name). Duplicate <code>box_number</code> values are reported as errors.
        </p>

        <form
          className="mt-4 space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setError(null);
            if (!file) {
              setError("Pick an .xlsx file to upload.");
              return;
            }
            try {
              const result = await importBoxes.mutateAsync({
                file,
                warehouse_id:
                  warehouseId === "" ? undefined : Number(warehouseId),
              });
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
            <span className="text-xs text-slate-500">XLSX file</span>
            <input
              required
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              className="input"
              onChange={(e) => {
                const next = e.target.files?.[0] ?? null;
                setFile(next);
              }}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">
              Default warehouse (optional)
            </span>
            <select
              className="input"
              value={warehouseId}
              onChange={(e) =>
                setWarehouseId(e.target.value ? Number(e.target.value) : "")
              }
            >
              <option value="">No default — column required per row</option>
              {warehouses.data?.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-xs text-slate-400">
              Used only when a row leaves the warehouse column blank.
            </span>
          </label>

          {error && <p className="text-sm text-rose-600">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
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
              disabled={importBoxes.isPending}
            >
              <Upload className="h-4 w-4" />
              {importBoxes.isPending ? "Importing..." : "Import"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
