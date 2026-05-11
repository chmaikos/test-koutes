import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  ChevronUp,
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
  BoxSortField,
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

// Whitelist for the page-size selector. The API enforces a 1..200
// range; we expose the four common buckets so operators can quickly
// scale the table density without typing into the URL bar.
const PAGE_SIZE_OPTIONS = [25, 50, 100, 200] as const;
type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];
const DEFAULT_PAGE_SIZE: PageSize = 25;

// Mirrors the API's linear transition map. We expose only the *next*
// step in the chain so the inline "Move to..." picker is unambiguous;
// admins who need to leapfrog still have the bulk action bar's
// "Override rules" affordance plus the box detail page.
const NEXT_STATUS: Record<BoxStatus, BoxStatus[]> = {
  received: ["processing"],
  processing: ["incomplete"],
  incomplete: ["ready_to_return"],
  ready_to_return: ["returned"],
  returned: [],
};

// Mirrors ``SORTABLE_FIELDS`` in the API. Used to validate the URL
// param before forwarding it to the hook so a stale link with a
// removed field doesn't trigger a 422.
const SORTABLE_FIELDS: ReadonlySet<BoxSortField> = new Set([
  "box_number",
  "lot",
  "status",
  "warehouse",
  "received_at",
  "updated_at",
]);

function isSortField(value: string | null): value is BoxSortField {
  return value !== null && SORTABLE_FIELDS.has(value as BoxSortField);
}

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
    const lot = params.get("lot");
    if (lot) out.lot = lot;
    const search = params.get("q");
    if (search) out.search = search;
    const sortBy = params.get("sort_by");
    if (isSortField(sortBy)) {
      out.sort_by = sortBy;
      const dir = params.get("sort_dir");
      out.sort_dir = dir === "asc" ? "asc" : "desc";
    }
    return out;
  }, [params]);

  const page = Number(params.get("page") ?? 1);
  const pageSize: PageSize = (() => {
    const raw = Number(params.get("page_size"));
    return (PAGE_SIZE_OPTIONS as readonly number[]).includes(raw)
      ? (raw as PageSize)
      : DEFAULT_PAGE_SIZE;
  })();
  const { data, isLoading } = useBoxes(filters, page, pageSize);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;

  const activeSort: BoxSortField | null = filters.sort_by ?? null;
  const activeSortDir: "asc" | "desc" = filters.sort_dir ?? "desc";

  function toggleSort(field: BoxSortField) {
    const next = new URLSearchParams(params);
    next.delete("page");
    if (activeSort !== field) {
      // First click on a new column: start at descending. That matches
      // how most data-grids behave and lines up with the existing
      // default order (``updated_at desc``).
      next.set("sort_by", field);
      next.set("sort_dir", "desc");
    } else if (activeSortDir === "desc") {
      next.set("sort_dir", "asc");
    } else {
      // Third click: drop the explicit sort, fall back to the API
      // default. This gives users a way to "undo" a sort without
      // hunting for a button.
      next.delete("sort_by");
      next.delete("sort_dir");
    }
    setParams(next);
  }

  function setPageSize(next: PageSize) {
    const params2 = new URLSearchParams(params);
    if (next === DEFAULT_PAGE_SIZE) {
      params2.delete("page_size");
    } else {
      params2.set("page_size", String(next));
    }
    params2.delete("page");
    setParams(params2);
  }

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
              placeholder="Box # or lot"
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
          <span className="text-xs text-slate-500">Lot</span>
          <input
            className="input"
            value={filters.lot ?? ""}
            onChange={(e) => setParam("lot", e.target.value || undefined)}
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
        <div className="hidden overflow-x-auto md:block">
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
                <SortableTh
                  label="Box #"
                  field="box_number"
                  activeField={activeSort}
                  direction={activeSortDir}
                  onToggle={toggleSort}
                />
                <SortableTh
                  label="Lot"
                  field="lot"
                  activeField={activeSort}
                  direction={activeSortDir}
                  onToggle={toggleSort}
                />
                <th className="px-4 py-2.5 text-left">Contents</th>
                <SortableTh
                  label="Warehouse"
                  field="warehouse"
                  activeField={activeSort}
                  direction={activeSortDir}
                  onToggle={toggleSort}
                />
                <SortableTh
                  label="Status"
                  field="status"
                  activeField={activeSort}
                  direction={activeSortDir}
                  onToggle={toggleSort}
                />
                <SortableTh
                  label="Received"
                  field="received_at"
                  activeField={activeSort}
                  direction={activeSortDir}
                  onToggle={toggleSort}
                />
                <SortableTh
                  label="Updated"
                  field="updated_at"
                  activeField={activeSort}
                  direction={activeSortDir}
                  onToggle={toggleSort}
                />
                <th className="px-4 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {isLoading && (
                <tr>
                  <td
                    className="px-4 py-8 text-center text-slate-400"
                    colSpan={canWrite ? 9 : 8}
                  >
                    Loading...
                  </td>
                </tr>
              )}
              {!isLoading && data?.items.length === 0 && (
                <tr>
                  <td
                    className="px-4 py-8 text-center text-slate-400"
                    colSpan={canWrite ? 9 : 8}
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
        <div className="divide-y divide-slate-100 md:hidden">
          {isLoading && (
            <div className="px-4 py-8 text-center text-slate-400">
              Loading...
            </div>
          )}
          {!isLoading && data?.items.length === 0 && (
            <div className="px-4 py-8 text-center text-slate-400">
              No boxes match your filters.
            </div>
          )}
          {data?.items.map((box) => (
            <BoxCard
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
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
          <div className="flex flex-wrap items-center gap-3">
            <span>
              Page {page} of {totalPages} · {data?.total ?? 0} total
              {selectedIds.size > 0 && (
                <span className="ml-2 text-slate-700">
                  · {selectedIds.size} selected
                </span>
              )}
            </span>
            <label className="inline-flex items-center gap-1.5">
              <span>Rows per page</span>
              <select
                className="input h-7 w-auto px-1 py-0 text-xs"
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value) as PageSize)}
                aria-label="Rows per page"
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
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

/**
 * Clickable column header for the boxes table.
 *
 * Renders the label plus one of three Lucide icons reflecting the
 * sort state for *this* column. The icon is part of the same
 * button so the whole header is the click target, which makes the
 * keyboard / pointer affordance obvious.
 */
function SortableTh({
  label,
  field,
  activeField,
  direction,
  onToggle,
}: {
  label: string;
  field: BoxSortField;
  activeField: BoxSortField | null;
  direction: "asc" | "desc";
  onToggle: (field: BoxSortField) => void;
}) {
  const isActive = activeField === field;
  const Icon = !isActive
    ? ChevronsUpDown
    : direction === "asc"
      ? ChevronUp
      : ChevronDown;
  const ariaSort: "ascending" | "descending" | "none" = !isActive
    ? "none"
    : direction === "asc"
      ? "ascending"
      : "descending";
  return (
    <th
      className="px-4 py-2.5 text-left"
      scope="col"
      aria-sort={ariaSort}
    >
      <button
        type="button"
        onClick={() => onToggle(field)}
        className={
          "inline-flex items-center gap-1 text-xs uppercase tracking-wider " +
          (isActive
            ? "text-slate-900"
            : "text-slate-500 hover:text-slate-700")
        }
      >
        {label}
        <Icon
          className={
            "h-3.5 w-3.5 " +
            (isActive ? "text-brand-600" : "text-slate-300")
          }
        />
      </button>
    </th>
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
    <div
      className={
        // Mobile: a true bottom sheet that hovers above the bottom-nav
        // tab bar (which itself respects the safe-area inset). Desktop:
        // the original sticky bar that pins to the top of the page.
        "fixed inset-x-0 bottom-16 z-30 flex flex-col gap-3 border-t border-brand-200 bg-brand-50/95 px-4 py-3 shadow-[0_-2px_12px_rgba(15,23,42,0.08)] backdrop-blur " +
        "pb-[max(0.75rem,env(safe-area-inset-bottom))] " +
        "md:static md:z-10 md:flex-row md:flex-wrap md:items-center md:gap-3 md:rounded-xl md:border md:border-brand-200 md:bg-brand-50/70 md:p-5 md:pb-5 md:shadow-sm md:backdrop-blur-0"
      }
    >
      <div className="flex items-center justify-between gap-3 md:contents">
        <div className="text-sm font-medium text-brand-800">
          {count} box{count === 1 ? "" : "es"} selected
        </div>
        <button
          type="button"
          className="btn-ghost md:order-last md:ml-auto"
          onClick={onClear}
          disabled={isPending}
        >
          <X className="h-4 w-4" /> Clear
        </button>
      </div>
      <div className="flex flex-col gap-2 md:flex-row md:items-center">
        <select
          className="input md:w-auto"
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
      <div className="flex flex-col gap-2 md:flex-row md:items-center">
        <select
          className="input md:w-auto"
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
          className="inline-flex items-center justify-center gap-1 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-50"
          disabled={isPending}
          onClick={onRequestDelete}
        >
          <Trash2 className="h-4 w-4" /> Delete selected
        </button>
      )}
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
      <td className="px-4 py-2.5">{box.lot}</td>
      <td className="px-4 py-2.5">
        {box.contents ? (
          <span
            className="block max-w-[18rem] truncate text-slate-700"
            title={box.contents}
          >
            {box.contents}
          </span>
        ) : (
          <span className="text-slate-400">—</span>
        )}
      </td>
      <td className="px-4 py-2.5">{warehouseName}</td>
      <td className="px-4 py-2.5">
        <StatusBadge status={box.status} />
      </td>
      <td className="px-4 py-2.5 text-slate-500">
        {box.received_at ? new Date(box.received_at).toLocaleString() : "—"}
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

function BoxCard({
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
    <div className="px-4 py-3">
      <div className="flex items-start gap-3">
        {canWrite && (
          <input
            type="checkbox"
            aria-label={`Select box ${box.box_number}`}
            checked={selected}
            onChange={onToggle}
            className="mt-1 h-4 w-4"
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <Link
              to={`/boxes/${box.id}`}
              className="truncate font-mono text-base font-semibold text-brand-700 hover:underline"
            >
              {box.box_number}
            </Link>
            <StatusBadge status={box.status} />
          </div>
          <dl className="mt-2 grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
            <dt className="text-slate-500">Lot</dt>
            <dd className="truncate text-slate-800">{box.lot}</dd>
            <dt className="text-slate-500">Warehouse</dt>
            <dd className="truncate text-slate-800">{warehouseName}</dd>
            {box.contents && (
              <>
                <dt className="text-slate-500">Contents</dt>
                <dd className="line-clamp-2 break-words text-slate-700">
                  {box.contents}
                </dd>
              </>
            )}
            <dt className="text-slate-500">Updated</dt>
            <dd className="truncate text-slate-500">
              {new Date(box.updated_at).toLocaleString()}
            </dd>
          </dl>
          {canWrite && transitions.length > 0 && (
            <div className="mt-3">
              <select
                className="input w-full text-sm"
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
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function CreateBoxModal({ onClose }: { onClose: () => void }) {
  const warehouses = useWarehouses();
  const create = useCreateBox();
  const [boxNumber, setBoxNumber] = useState("");
  const [lot, setLot] = useState("");
  const [contents, setContents] = useState("");
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="modal-backdrop z-30">
      <div className="modal-sheet max-w-md">
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
              const trimmedContents = contents.trim();
              const trimmedNumber = boxNumber.trim();
              // Mirror the server-side rule: numeric only, zero-padded to
              // a minimum of 3 digits. Server stays authoritative; this
              // just keeps the optimistic UI in sync with what the API
              // will ultimately persist.
              const normalizedNumber = /^[0-9]+$/.test(trimmedNumber)
                ? trimmedNumber.padStart(3, "0")
                : trimmedNumber;
              await create.mutateAsync({
                box_number: normalizedNumber,
                lot: lot.trim(),
                contents: trimmedContents || undefined,
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
            <span className="text-xs text-slate-500">
              Box number (numeric, padded to 3 digits)
            </span>
            <input
              required
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={64}
              placeholder="e.g. 001"
              className="input"
              value={boxNumber}
              onChange={(e) => setBoxNumber(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Lot</span>
            <input
              required
              maxLength={64}
              className="input"
              value={lot}
              onChange={(e) => setLot(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500">Contents (optional)</span>
            <textarea
              maxLength={200}
              rows={2}
              className="input"
              value={contents}
              onChange={(e) => setContents(e.target.value)}
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
          <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
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
