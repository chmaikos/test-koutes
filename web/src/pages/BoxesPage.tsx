import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ChevronLeft,
  ChevronRight,
  Plus,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  useBoxes,
  useBulkDeleteBoxes,
  useBulkUpdateBoxes,
  useCreateBox,
  useUpdateBox,
  useWarehouses,
} from "@/api/hooks";
import type {
  BoxFilters,
  BoxStatus,
  BulkDeleteResult,
  BulkResult,
  ImportResult,
} from "@/api/types";
import { ALL_BOX_STATUSES } from "@/api/types";
import { STATUS_LABEL, StatusBadge } from "@/components/StatusBadge";
import {
  BulkResultDialog,
  type BulkResultSkipRow,
} from "@/components/BulkResultDialog";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ImportBoxesDialog } from "@/components/ImportBoxesDialog";
import { useHasRole } from "@/components/RoleGate";

const PAGE_SIZE = 25;

const NEXT_STATUS: Record<BoxStatus, BoxStatus[]> = {
  received: ["ready_to_return"],
  ready_to_return: ["returned"],
  returned: [],
};

type DialogState =
  | { kind: "bulk"; result: BulkResult }
  | { kind: "import"; result: ImportResult }
  | { kind: "delete"; result: BulkDeleteResult }
  | null;

export function BoxesPage() {
  const [params, setParams] = useSearchParams();
  const canWrite = useHasRole(["admin", "operator"]);
  const isAdmin = useHasRole(["admin"]);
  const warehouses = useWarehouses();

  const filters: BoxFilters = useMemo(() => {
    const out: BoxFilters = {};
    const wid = params.get("warehouse_id");
    if (wid) out.warehouse_id = Number(wid);
    const st = params.get("status");
    if (st) out.status = st as BoxStatus;
    const owner = params.get("owner");
    if (owner) out.owner = owner;
    const search = params.get("q");
    if (search) out.search = search;
    return out;
  }, [params]);

  const page = Number(params.get("page") ?? 1);
  const { data, isLoading } = useBoxes(filters, page, PAGE_SIZE);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [dialog, setDialog] = useState<DialogState>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const bulkUpdate = useBulkUpdateBoxes();
  const bulkDelete = useBulkDeleteBoxes();

  const visibleIds = useMemo(
    () => data?.items.map((b) => b.id) ?? [],
    [data?.items],
  );
  const visibleSelectedCount = visibleIds.filter((id) =>
    selectedIds.has(id),
  ).length;
  const allVisibleSelected =
    visibleIds.length > 0 && visibleSelectedCount === visibleIds.length;
  const someVisibleSelected =
    visibleSelectedCount > 0 && !allVisibleSelected;

  const headerCheckboxRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (headerCheckboxRef.current) {
      headerCheckboxRef.current.indeterminate = someVisibleSelected;
    }
  }, [someVisibleSelected]);

  function setParam(name: string, value: string | undefined) {
    const next = new URLSearchParams(params);
    if (!value) next.delete(name);
    else next.set(name, value);
    next.delete("page");
    setParams(next);
  }

  function toggleId(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function togglePage() {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        for (const id of visibleIds) next.delete(id);
      } else {
        for (const id of visibleIds) next.add(id);
      }
      return next;
    });
  }

  function clearSelection() {
    setSelectedIds(new Set());
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Boxes</h1>
          <p className="text-sm text-slate-500">
            Search, filter, and update boxes across warehouses.
          </p>
        </div>
        {canWrite && (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setShowImport(true)}
            >
              <Upload className="h-4 w-4" /> Import XLSX
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => setShowCreate(true)}
            >
              <Plus className="h-4 w-4" /> Receive new box
            </button>
          </div>
        )}
      </header>

      <div className="card card-pad grid gap-3 md:grid-cols-4">
        <label className="block">
          <span className="text-xs text-slate-500">Search</span>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-slate-400" />
            <input
              className="input pl-8"
              placeholder="Box # or owner"
              value={filters.search ?? ""}
              onChange={(e) => setParam("q", e.target.value || undefined)}
            />
          </div>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Warehouse</span>
          <select
            className="input"
            value={filters.warehouse_id ?? ""}
            onChange={(e) =>
              setParam("warehouse_id", e.target.value || undefined)
            }
          >
            <option value="">All</option>
            {warehouses.data?.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Status</span>
          <select
            className="input"
            value={filters.status ?? ""}
            onChange={(e) => setParam("status", e.target.value || undefined)}
          >
            <option value="">All</option>
            {ALL_BOX_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Owner</span>
          <input
            className="input"
            value={filters.owner ?? ""}
            onChange={(e) => setParam("owner", e.target.value || undefined)}
          />
        </label>
      </div>

      {canWrite && selectedIds.size > 0 && (
        <BulkActionBar
          count={selectedIds.size}
          warehouses={warehouses.data ?? []}
          isPending={bulkUpdate.isPending || bulkDelete.isPending}
          isAdmin={isAdmin}
          onClear={clearSelection}
          onApply={async (input) => {
            const result = await bulkUpdate.mutateAsync({
              box_ids: Array.from(selectedIds),
              ...input,
            });
            setDialog({ kind: "bulk", result });
            clearSelection();
          }}
          onRequestDelete={() => setConfirmDelete(true)}
        />
      )}

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
              <tr>
                {canWrite && (
                  <th className="w-10 px-3 py-2.5 text-left">
                    <input
                      ref={headerCheckboxRef}
                      type="checkbox"
                      aria-label="Select all on this page"
                      checked={allVisibleSelected}
                      onChange={togglePage}
                      disabled={visibleIds.length === 0}
                    />
                  </th>
                )}
                <th className="px-4 py-2.5 text-left">Box #</th>
                <th className="px-4 py-2.5 text-left">Owner</th>
                <th className="px-4 py-2.5 text-left">Warehouse</th>
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-left">Updated</th>
                <th className="px-4 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {isLoading && (
                <tr>
                  <td
                    className="px-4 py-8 text-center text-slate-400"
                    colSpan={canWrite ? 7 : 6}
                  >
                    Loading...
                  </td>
                </tr>
              )}
              {!isLoading && data?.items.length === 0 && (
                <tr>
                  <td
                    className="px-4 py-8 text-center text-slate-400"
                    colSpan={canWrite ? 7 : 6}
                  >
                    No boxes match your filters.
                  </td>
                </tr>
              )}
              {data?.items.map((box) => (
                <BoxRow
                  key={box.id}
                  box={box}
                  warehouseName={
                    warehouses.data?.find((w) => w.id === box.current_warehouse_id)
                      ?.name ?? `#${box.current_warehouse_id}`
                  }
                  canWrite={canWrite}
                  selected={selectedIds.has(box.id)}
                  onToggle={() => toggleId(box.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
          <div>
            Page {page} of {totalPages} · {data?.total ?? 0} total
            {selectedIds.size > 0 && (
              <span className="ml-2 text-slate-700">
                · {selectedIds.size} selected
              </span>
            )}
          </div>
          <div className="flex gap-1">
            <button
              type="button"
              className="btn-ghost"
              disabled={page <= 1}
              onClick={() => {
                const next = new URLSearchParams(params);
                next.set("page", String(page - 1));
                setParams(next);
              }}
            >
              <ChevronLeft className="h-4 w-4" /> Prev
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={page >= totalPages}
              onClick={() => {
                const next = new URLSearchParams(params);
                next.set("page", String(page + 1));
                setParams(next);
              }}
            >
              Next <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {showCreate && <CreateBoxModal onClose={() => setShowCreate(false)} />}
      {showImport && (
        <ImportBoxesDialog
          onClose={() => setShowImport(false)}
          onResult={(result) => setDialog({ kind: "import", result })}
        />
      )}
      {dialog?.kind === "bulk" && (
        <BulkResultDialog
          title="Bulk update result"
          successLabel="updated"
          successCount={dialog.result.updated.length}
          skipped={dialog.result.skipped.map<BulkResultSkipRow>((s) => ({
            primary: s.box_number || `#${s.box_id}`,
            secondary: s.box_number ? `#${s.box_id}` : undefined,
            reason: s.reason,
          }))}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === "import" && (
        <BulkResultDialog
          title="Import result"
          successLabel="imported"
          successCount={dialog.result.created.length}
          skipped={dialog.result.skipped.map<BulkResultSkipRow>((s) => ({
            primary: `Row ${s.row}`,
            secondary: s.box_number ?? undefined,
            reason: s.reason,
          }))}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === "delete" && (
        <BulkResultDialog
          title="Delete result"
          successLabel="deleted"
          successCount={dialog.result.deleted_ids.length}
          skipped={dialog.result.skipped.map<BulkResultSkipRow>((s) => ({
            primary: s.box_number || `#${s.box_id}`,
            secondary: s.box_number ? `#${s.box_id}` : undefined,
            reason: s.reason,
          }))}
          onClose={() => setDialog(null)}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          title={`Delete ${selectedIds.size} box${
            selectedIds.size === 1 ? "" : "es"
          }?`}
          message="Deleted boxes are removed permanently along with their event history. This cannot be undone."
          confirmLabel="Delete"
          destructive
          isPending={bulkDelete.isPending}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={async () => {
            const ids = Array.from(selectedIds);
            const result = await bulkDelete.mutateAsync(ids);
            setConfirmDelete(false);
            setDialog({ kind: "delete", result });
            clearSelection();
          }}
        />
      )}
    </div>
  );
}

function BulkActionBar({
  count,
  warehouses,
  isPending,
  isAdmin,
  onClear,
  onApply,
  onRequestDelete,
}: {
  count: number;
  warehouses: { id: number; name: string }[];
  isPending: boolean;
  isAdmin: boolean;
  onClear: () => void;
  onApply: (input: {
    warehouse_id?: number;
    status?: BoxStatus;
    force?: boolean;
  }) => Promise<void>;
  onRequestDelete: () => void;
}) {
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [statusValue, setStatusValue] = useState<BoxStatus | "">("");
  const [override, setOverride] = useState(false);

  return (
    <div className="sticky top-0 z-10 card card-pad flex flex-wrap items-center gap-3 border-brand-200 bg-brand-50/70">
      <div className="text-sm font-medium text-brand-800">
        {count} box{count === 1 ? "" : "es"} selected
      </div>
      <div className="flex items-center gap-2">
        <select
          className="input w-auto"
          value={warehouseId}
          onChange={(e) =>
            setWarehouseId(e.target.value ? Number(e.target.value) : "")
          }
          disabled={isPending}
        >
          <option value="">Move to warehouse...</option>
          {warehouses.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn-primary"
          disabled={!warehouseId || isPending}
          onClick={async () => {
            if (!warehouseId) return;
            await onApply({
              warehouse_id: Number(warehouseId),
              force: isAdmin && override ? true : undefined,
            });
            setWarehouseId("");
          }}
        >
          Move {count}
        </button>
      </div>
      <div className="flex items-center gap-2">
        <select
          className="input w-auto"
          value={statusValue}
          onChange={(e) => setStatusValue(e.target.value as BoxStatus | "")}
          disabled={isPending}
        >
          <option value="">Change status to...</option>
          {ALL_BOX_STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn-primary"
          disabled={!statusValue || isPending}
          onClick={async () => {
            if (!statusValue) return;
            await onApply({
              status: statusValue as BoxStatus,
              force: isAdmin && override ? true : undefined,
            });
            setStatusValue("");
          }}
        >
          Apply status
        </button>
      </div>
      {isAdmin && (
        <label
          className="flex items-center gap-1.5 text-xs text-amber-800"
          title="Allow any status change or moving a returned box. Audit log will tag the change as [admin override]."
        >
          <input
            type="checkbox"
            checked={override}
            onChange={(e) => setOverride(e.target.checked)}
            disabled={isPending}
          />
          Override rules (admin)
        </label>
      )}
      {isAdmin && (
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-50"
          disabled={isPending}
          onClick={onRequestDelete}
        >
          <Trash2 className="h-4 w-4" /> Delete selected
        </button>
      )}
      <button
        type="button"
        className="btn-ghost ml-auto"
        onClick={onClear}
        disabled={isPending}
      >
        <X className="h-4 w-4" /> Clear
      </button>
    </div>
  );
}

function BoxRow({
  box,
  warehouseName,
  canWrite,
  selected,
  onToggle,
}: {
  box: import("@/api/types").Box;
  warehouseName: string;
  canWrite: boolean;
  selected: boolean;
  onToggle: () => void;
}) {
  const update = useUpdateBox();
  const transitions = NEXT_STATUS[box.status] ?? [];
  return (
    <tr className="hover:bg-slate-50">
      {canWrite && (
        <td className="px-3 py-2.5">
          <input
            type="checkbox"
            aria-label={`Select box ${box.box_number}`}
            checked={selected}
            onChange={onToggle}
          />
        </td>
      )}
      <td className="px-4 py-2.5 font-mono">
        <Link className="text-brand-700 hover:underline" to={`/boxes/${box.id}`}>
          {box.box_number}
        </Link>
      </td>
      <td className="px-4 py-2.5">{box.owner || <span className="text-slate-400">—</span>}</td>
      <td className="px-4 py-2.5">{warehouseName}</td>
      <td className="px-4 py-2.5">
        <StatusBadge status={box.status} />
      </td>
      <td className="px-4 py-2.5 text-slate-500">
        {new Date(box.updated_at).toLocaleString()}
      </td>
      <td className="px-4 py-2.5 text-right">
        {canWrite && transitions.length > 0 && (
          <select
            className="input inline-block w-auto text-xs"
            disabled={update.isPending}
            value=""
            onChange={(e) => {
              const status = e.target.value as BoxStatus;
              if (!status) return;
              update.mutate({ id: box.id, patch: { status } });
            }}
          >
            <option value="">Move to...</option>
            {transitions.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        )}
      </td>
    </tr>
  );
}

function CreateBoxModal({ onClose }: { onClose: () => void }) {
  const warehouses = useWarehouses();
  const create = useCreateBox();
  const [boxNumber, setBoxNumber] = useState("");
  const [owner, setOwner] = useState("");
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="card card-pad w-full max-w-md">
        <h2 className="text-lg font-semibold">Receive a new box</h2>
        <form
          className="mt-4 space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setError(null);
            if (!warehouseId) {
              setError("Pick a warehouse");
              return;
            }
            try {
              await create.mutateAsync({
                box_number: boxNumber.trim(),
                owner: owner.trim(),
                warehouse_id: Number(warehouseId),
              });
              onClose();
            } catch (err: unknown) {
              const detail =
                (err as { response?: { data?: { detail?: string } } })?.response?.data
                  ?.detail ?? "Failed to create box";
              setError(detail);
            }
          }}
        >
          <label className="block">
            <span className="text-xs text-slate-500">Box number</span>
            <input
              required
              className="input"
              value={boxNumber}
              onChange={(e) => setBoxNumber(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Owner / customer</span>
            <input
              className="input"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Warehouse</span>
            <select
              required
              className="input"
              value={warehouseId}
              onChange={(e) =>
                setWarehouseId(e.target.value ? Number(e.target.value) : "")
              }
            >
              <option value="">Pick a warehouse</option>
              {warehouses.data?.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
          {error && <p className="text-sm text-rose-600">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              className="btn-secondary"
              onClick={onClose}
              disabled={create.isPending}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={create.isPending}
            >
              {create.isPending ? "Saving..." : "Receive"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
