import { useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Mail,
  Plus,
  Trash2,
  Undo2,
  Upload,
} from "lucide-react";
import {
  useAlertRecipients,
  useArchiveWarehouse,
  useCreateEmployee,
  useCreateWarehouse,
  useDeleteEmployee,
  useEmployees,
  useRestoreWarehouse,
  useUpdateEmployee,
  useUpdateUser,
  useUpdateWarehouse,
  useUsers,
  useWarehouses,
} from "@/api/hooks";
import type { Employee, Role, User, Warehouse } from "@/api/types";
import { ImportEmployeesDialog } from "@/components/ImportEmployeesDialog";
import { warehouseArchiveError } from "@/pages/warehouseArchive";

const EMPLOYEES_PAGE_SIZE = 25;

export function SettingsPage() {
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-slate-500">
          Admin-only: warehouse thresholds, user access, and employees.
        </p>
      </header>

      <WarehousesSection />
      <EmployeesSection />
      <UsersSection />
      <RecipientsPreview />
    </div>
  );
}

function EmployeesSection() {
  const { data: warehouses } = useWarehouses();
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [page, setPage] = useState(1);
  const create = useCreateEmployee();
  const employees = useEmployees(
    typeof warehouseId === "number" ? warehouseId : undefined,
    includeInactive,
    { page, pageSize: EMPLOYEES_PAGE_SIZE },
  );
  // Resetting the page whenever the filters change keeps the user from
  // landing on a (now-empty) page after narrowing the roster down.
  useEffect(() => {
    setPage(1);
  }, [warehouseId, includeInactive]);

  const sortedWarehouses = useMemo(
    () => warehouses ?? [],
    [warehouses],
  );

  const items = employees.data?.items ?? [];
  const total = employees.data?.total ?? 0;
  const pageSize = employees.data?.page_size ?? EMPLOYEES_PAGE_SIZE;
  const totalPages = total === 0 ? 1 : Math.ceil(total / pageSize);

  return (
    <>
    <section className="card overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
        <div>
          <h2 className="font-semibold">Employees</h2>
          <p className="text-xs text-slate-500">
            Roster used by the productivity tracker. Set their working hours
            here so the daily entry form is pre-filled.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="input max-w-[180px]"
            value={warehouseId}
            onChange={(e) =>
              setWarehouseId(e.target.value === "" ? "" : Number(e.target.value))
            }
          >
            <option value="">All warehouses</option>
            {sortedWarehouses.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
          <label className="inline-flex items-center gap-1.5 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
            />
            Include inactive
          </label>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setShowImport(true)}
          >
            <Upload className="h-4 w-4" />
            Import
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setShowAdd((v) => !v)}
          >
            <Plus className="h-4 w-4" />
            {showAdd ? "Cancel" : "Add employee"}
          </button>
        </div>
      </header>
      {showAdd && (
        <NewEmployeeForm
          warehouses={sortedWarehouses}
          defaultWarehouseId={
            typeof warehouseId === "number"
              ? warehouseId
              : (sortedWarehouses[0]?.id ?? null)
          }
          onCreate={async (input) => {
            await create.mutateAsync(input);
            setShowAdd(false);
          }}
          onCancel={() => setShowAdd(false)}
        />
      )}
      {employees.isLoading && items.length === 0 ? (
        <p className="px-5 py-4 text-sm text-slate-500">Loading...</p>
      ) : items.length === 0 ? (
        <p className="px-5 py-4 text-sm text-slate-500">
          No employees yet. Add one above.
        </p>
      ) : (
        <>
          <div className="divide-y divide-slate-100">
            {items.map((e) => (
              <EmployeeRow
                key={e.id}
                employee={e}
                warehouses={sortedWarehouses}
              />
            ))}
          </div>
          <EmployeesPagination
            page={page}
            totalPages={totalPages}
            total={total}
            onChange={setPage}
          />
        </>
      )}
    </section>
    {showImport && (
      <ImportEmployeesDialog
        warehouses={sortedWarehouses}
        defaultWarehouseId={
          typeof warehouseId === "number"
            ? warehouseId
            : (sortedWarehouses[0]?.id ?? null)
        }
        onClose={() => setShowImport(false)}
      />
    )}
    </>
  );
}

function EmployeesPagination({
  page,
  totalPages,
  total,
  onChange,
}: {
  page: number;
  totalPages: number;
  total: number;
  onChange: (page: number) => void;
}) {
  if (totalPages <= 1 && total <= 0) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 bg-slate-50/40 px-5 py-2.5 text-xs text-slate-500">
      <span>
        Page <span className="font-medium text-slate-700">{page}</span> of{" "}
        <span className="font-medium text-slate-700">{totalPages}</span>
        <span className="ml-2 text-slate-400">({total} total)</span>
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          className="btn-ghost px-2 py-1 disabled:opacity-40"
          onClick={() => onChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          aria-label="Previous page"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <button
          type="button"
          className="btn-ghost px-2 py-1 disabled:opacity-40"
          onClick={() => onChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          aria-label="Next page"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function NewEmployeeForm({
  warehouses,
  defaultWarehouseId,
  onCreate,
  onCancel,
}: {
  warehouses: Warehouse[];
  defaultWarehouseId: number | null;
  onCreate: (input: {
    warehouse_id: number;
    full_name: string;
    default_hours_per_day?: number;
    excluded_from_metrics?: boolean;
  }) => Promise<unknown>;
  onCancel: () => void;
}) {
  const [warehouseId, setWarehouseId] = useState<number | null>(
    defaultWarehouseId,
  );
  const [fullName, setFullName] = useState("");
  const [hours, setHours] = useState<number>(8);
  const [excluded, setExcluded] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="grid gap-3 border-b border-slate-100 bg-slate-50/60 px-5 py-4 sm:grid-cols-[1fr_2fr_1fr_auto] sm:items-end">
      <label className="block">
        <span className="text-xs text-slate-500">Warehouse</span>
        <select
          className="input"
          value={warehouseId ?? ""}
          onChange={(e) =>
            setWarehouseId(e.target.value === "" ? null : Number(e.target.value))
          }
        >
          {warehouses.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Full name</span>
        <input
          className="input"
          autoFocus
          value={fullName}
          placeholder="Jane Doe"
          onChange={(e) => setFullName(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Hours / day</span>
        <input
          type="number"
          className="input"
          min={0.25}
          max={24}
          step={0.25}
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-primary"
          disabled={
            pending || !fullName.trim() || !warehouseId || hours <= 0
          }
          onClick={async () => {
            if (!warehouseId) return;
            setPending(true);
            setError(null);
            try {
              await onCreate({
                warehouse_id: warehouseId,
                full_name: fullName.trim(),
                default_hours_per_day: hours,
                excluded_from_metrics: excluded,
              });
              setFullName("");
              setExcluded(false);
            } catch (err: unknown) {
              const detail =
                (err as { response?: { data?: { detail?: string } } })?.response
                  ?.data?.detail ?? "Failed to create employee";
              setError(typeof detail === "string" ? detail : "Failed to create employee");
            } finally {
              setPending(false);
            }
          }}
        >
          {pending ? "Creating..." : "Create"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={pending}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
      <label className="inline-flex items-center gap-2 text-xs text-slate-600 sm:col-span-4">
        <input
          type="checkbox"
          checked={excluded}
          onChange={(e) => setExcluded(e.target.checked)}
        />
        <span>
          Exclude from metrics
          <span className="ml-1 text-slate-400">
            (admin override — pulls every entry out of the totals)
          </span>
        </span>
      </label>
      {error && <p className="text-xs text-rose-600 sm:col-span-4">{error}</p>}
    </div>
  );
}

function EmployeeRow({
  employee,
  warehouses,
}: {
  employee: Employee;
  warehouses: Warehouse[];
}) {
  const update = useUpdateEmployee();
  const remove = useDeleteEmployee();
  const [fullName, setFullName] = useState(employee.full_name);
  const [hours, setHours] = useState<number>(
    Number(employee.default_hours_per_day) || 8,
  );
  const [warehouseId, setWarehouseId] = useState<number>(employee.warehouse_id);
  const [excluded, setExcluded] = useState<boolean>(employee.excluded_from_metrics);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    fullName !== employee.full_name ||
    Math.abs(hours - Number(employee.default_hours_per_day)) > 0.001 ||
    warehouseId !== employee.warehouse_id ||
    excluded !== employee.excluded_from_metrics;

  return (
    <div className="grid gap-3 px-5 py-3 sm:grid-cols-[1fr_2fr_1fr_auto] sm:items-end">
      <label className="block">
        <span className="text-xs text-slate-500">Warehouse</span>
        <select
          className="input"
          value={warehouseId}
          onChange={(e) => setWarehouseId(Number(e.target.value))}
        >
          {warehouses.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">
          Full name {!employee.is_active && (
            <span className="ml-1 rounded-full bg-slate-200 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-slate-700">
              Inactive
            </span>
          )}
          {employee.excluded_from_metrics && (
            <span className="ml-1 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-amber-800">
              Excluded
            </span>
          )}
        </span>
        <input
          className="input"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Hours / day</span>
        <input
          type="number"
          className="input"
          min={0.25}
          max={24}
          step={0.25}
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-primary"
          disabled={!dirty || update.isPending}
          onClick={async () => {
            setError(null);
            try {
              await update.mutateAsync({
                id: employee.id,
                patch: {
                  full_name: fullName,
                  default_hours_per_day: hours,
                  warehouse_id: warehouseId,
                  excluded_from_metrics: excluded,
                },
              });
            } catch (err: unknown) {
              const detail =
                (err as { response?: { data?: { detail?: string } } })?.response
                  ?.data?.detail ?? "Failed to save";
              setError(typeof detail === "string" ? detail : "Failed to save");
            }
          }}
        >
          {update.isPending ? "Saving..." : "Save"}
        </button>
        {employee.is_active ? (
          <button
            type="button"
            className="btn-ghost text-rose-600 hover:bg-rose-50"
            title="Deactivate employee"
            onClick={() => {
              if (
                window.confirm(
                  `Deactivate ${employee.full_name}? Historical productivity entries are kept.`,
                )
              ) {
                void remove.mutateAsync(employee.id);
              }
            }}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        ) : (
          <button
            type="button"
            className="btn-ghost text-emerald-700 hover:bg-emerald-50"
            title="Reactivate employee"
            onClick={() =>
              void update.mutateAsync({
                id: employee.id,
                patch: { is_active: true },
              })
            }
          >
            <Undo2 className="h-4 w-4" />
          </button>
        )}
      </div>
      <label className="inline-flex items-center gap-2 text-xs text-slate-600 sm:col-span-4">
        <input
          type="checkbox"
          checked={excluded}
          onChange={(e) => setExcluded(e.target.checked)}
        />
        <span>
          Exclude from metrics
          <span className="ml-1 text-slate-400">
            (admin override — pulls every entry out of the totals)
          </span>
        </span>
      </label>
      {error && <p className="text-xs text-rose-600 sm:col-span-4">{error}</p>}
    </div>
  );
}

function WarehousesSection() {
  const [includeInactive, setIncludeInactive] = useState(false);
  const { data } = useWarehouses(includeInactive);
  const update = useUpdateWarehouse();
  const create = useCreateWarehouse();
  const archive = useArchiveWarehouse();
  const restore = useRestoreWarehouse();
  const [showAdd, setShowAdd] = useState(false);

  return (
    <section className="card overflow-hidden">
      <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-3">
        <div>
          <h2 className="font-semibold">Warehouses & thresholds</h2>
          <p className="text-xs text-slate-500">
            Configure inventory limits and the minimum expected productivity
            rate for each warehouse.
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          <label className="inline-flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(event) => setIncludeInactive(event.target.checked)}
            />
            Include archived
          </label>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setShowAdd((v) => !v)}
          >
            <Plus className="h-4 w-4" />
            {showAdd ? "Cancel" : "Add warehouse"}
          </button>
        </div>
      </header>
      {showAdd && (
        <NewWarehouseForm
          onCreate={async (input) => {
            await create.mutateAsync(input);
            setShowAdd(false);
          }}
          onCancel={() => setShowAdd(false)}
        />
      )}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
            <tr>
              <th className="px-4 py-2.5 text-left">Name</th>
              <th className="px-4 py-2.5 text-left">Min inventory</th>
              <th className="px-4 py-2.5 text-left">Max capacity</th>
              <th className="px-4 py-2.5 text-left">Min pages / day</th>
              <th className="px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data?.map((w) => (
              <WarehouseRow
                key={w.id}
                warehouse={w}
                onSave={(patch) => update.mutateAsync({ id: w.id, patch })}
                onArchive={() => archive.mutateAsync(w.id)}
                onRestore={() => restore.mutateAsync(w.id)}
              />
            ))}
          </tbody>
        </table>
      </div>
      <div className="divide-y divide-slate-100 md:hidden">
        {data?.map((w) => (
          <WarehouseCard
            key={w.id}
            warehouse={w}
            onSave={(patch) => update.mutateAsync({ id: w.id, patch })}
            onArchive={() => archive.mutateAsync(w.id)}
            onRestore={() => restore.mutateAsync(w.id)}
          />
        ))}
      </div>
    </section>
  );
}

function NewWarehouseForm({
  onCreate,
  onCancel,
}: {
  onCreate: (input: {
    name: string;
    min_inventory: number;
    max_capacity: number;
    min_pages_per_day: number | null;
  }) => Promise<unknown>;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [minInv, setMinInv] = useState(0);
  const [maxCap, setMaxCap] = useState(1000);
  const [minPages, setMinPages] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="grid gap-3 border-b border-slate-100 bg-slate-50/60 px-5 py-4 sm:grid-cols-[2fr_1fr_1fr_1fr_auto] sm:items-end">
      <label className="block">
        <span className="text-xs text-slate-500">Name</span>
        <input
          className="input"
          autoFocus
          value={name}
          placeholder="Building 4"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Min inventory</span>
        <input
          type="number"
          className="input"
          min={0}
          value={minInv}
          onChange={(e) => setMinInv(Number(e.target.value))}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Max capacity</span>
        <input
          type="number"
          className="input"
          min={1}
          value={maxCap}
          onChange={(e) => setMaxCap(Number(e.target.value))}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Min pages / day</span>
        <input
          type="number"
          className="input"
          min={1}
          step={1}
          value={minPages}
          placeholder="Not set"
          onChange={(e) => setMinPages(e.target.value)}
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-primary"
          disabled={pending || !name.trim()}
          onClick={async () => {
            setPending(true);
            setError(null);
            try {
              await onCreate({
                name: name.trim(),
                min_inventory: minInv,
                max_capacity: maxCap,
                min_pages_per_day: minPages === "" ? null : Number(minPages),
              });
            } catch (err: unknown) {
              const detail =
                (err as { response?: { data?: { detail?: string } } })?.response
                  ?.data?.detail ?? "Failed to create warehouse";
              setError(typeof detail === "string" ? detail : "Failed to create warehouse");
            } finally {
              setPending(false);
            }
          }}
        >
          {pending ? "Creating..." : "Create"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={pending}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
      {error && (
        <p className="text-xs text-rose-600 sm:col-span-5">{error}</p>
      )}
    </div>
  );
}

type WarehousePatch = {
  name?: string;
  min_inventory?: number;
  max_capacity?: number;
  min_pages_per_day?: number | null;
};

/**
 * Shared edit state for the desktop row and the mobile card so a save
 * pending in one layout reflects in the other. Returning JSX-ready
 * inputs keeps the markup intentionally similar.
 */
function useWarehouseEditor(
  warehouse: Warehouse,
  onSave: (patch: WarehousePatch) => Promise<unknown>,
) {
  const [name, setName] = useState(warehouse.name);
  const [minInv, setMinInv] = useState(warehouse.min_inventory);
  const [maxCap, setMaxCap] = useState(warehouse.max_capacity);
  const [minPages, setMinPages] = useState(
    warehouse.min_pages_per_day?.toString() ?? "",
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    name !== warehouse.name ||
    minInv !== warehouse.min_inventory ||
    maxCap !== warehouse.max_capacity ||
    minPages !== (warehouse.min_pages_per_day?.toString() ?? "");

  async function save() {
    setPending(true);
    setError(null);
    try {
      await onSave({
        name,
        min_inventory: minInv,
        max_capacity: maxCap,
        min_pages_per_day: minPages === "" ? null : Number(minPages),
      });
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Failed to save";
      setError(typeof detail === "string" ? detail : "Failed to save");
    } finally {
      setPending(false);
    }
  }

  return {
    name,
    setName,
    minInv,
    setMinInv,
    maxCap,
    setMaxCap,
    minPages,
    setMinPages,
    pending,
    error,
    dirty,
    save,
  };
}

function useWarehouseLifecycle(
  warehouse: Warehouse,
  onArchive: () => Promise<unknown>,
  onRestore: () => Promise<unknown>,
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: "archive" | "restore") {
    if (
      action === "archive" &&
      !window.confirm(
        `Archive ${warehouse.name}? Its inventory and history will be preserved.`,
      )
    ) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await (action === "archive" ? onArchive() : onRestore());
    } catch (err: unknown) {
      setError(warehouseArchiveError(err));
    } finally {
      setPending(false);
    }
  }

  return { pending, error, run };
}

function WarehouseRow({
  warehouse,
  onSave,
  onArchive,
  onRestore,
}: {
  warehouse: Warehouse;
  onSave: (patch: WarehousePatch) => Promise<unknown>;
  onArchive: () => Promise<unknown>;
  onRestore: () => Promise<unknown>;
}) {
  const ed = useWarehouseEditor(warehouse, onSave);
  const lifecycle = useWarehouseLifecycle(warehouse, onArchive, onRestore);
  return (
    <tr>
      <td className="px-4 py-2.5">
        <div className="flex items-center gap-2">
          <input
            className="input"
            value={ed.name}
            disabled={!warehouse.is_active}
            onChange={(e) => ed.setName(e.target.value)}
          />
          {!warehouse.is_active && (
            <span className="badge bg-slate-100 text-slate-600">Archived</span>
          )}
        </div>
      </td>
      <td className="px-4 py-2.5">
        <input
          type="number"
          className="input"
          min={0}
          value={ed.minInv}
          disabled={!warehouse.is_active}
          onChange={(e) => ed.setMinInv(Number(e.target.value))}
        />
      </td>
      <td className="px-4 py-2.5">
        <input
          type="number"
          className="input"
          min={1}
          value={ed.maxCap}
          disabled={!warehouse.is_active}
          onChange={(e) => ed.setMaxCap(Number(e.target.value))}
        />
      </td>
      <td className="px-4 py-2.5">
        <input
          type="number"
          className="input"
          min={1}
          step={1}
          value={ed.minPages}
          placeholder="Not set"
          disabled={!warehouse.is_active}
          onChange={(e) => ed.setMinPages(e.target.value)}
        />
      </td>
      <td className="px-4 py-2.5 text-right">
        {ed.error && (
          <span className="mr-2 text-xs text-rose-600">{ed.error}</span>
        )}
        {lifecycle.error && (
          <span className="mr-2 text-xs text-rose-600">{lifecycle.error}</span>
        )}
        <button
          type="button"
          className="btn-primary"
          disabled={!warehouse.is_active || !ed.dirty || ed.pending}
          onClick={() => void ed.save()}
        >
          {ed.pending ? "Saving..." : "Save"}
        </button>
        <button
          type="button"
          className="btn-secondary ml-2"
          disabled={lifecycle.pending}
          onClick={() =>
            void lifecycle.run(warehouse.is_active ? "archive" : "restore")
          }
        >
          {warehouse.is_active ? (
            <Trash2 className="h-4 w-4" />
          ) : (
            <Undo2 className="h-4 w-4" />
          )}
          {lifecycle.pending
            ? "Working..."
            : warehouse.is_active
              ? "Archive"
              : "Restore"}
        </button>
      </td>
    </tr>
  );
}

function WarehouseCard({
  warehouse,
  onSave,
  onArchive,
  onRestore,
}: {
  warehouse: Warehouse;
  onSave: (patch: WarehousePatch) => Promise<unknown>;
  onArchive: () => Promise<unknown>;
  onRestore: () => Promise<unknown>;
}) {
  const ed = useWarehouseEditor(warehouse, onSave);
  const lifecycle = useWarehouseLifecycle(warehouse, onArchive, onRestore);
  return (
    <div className="space-y-3 px-4 py-3">
      {!warehouse.is_active && (
        <span className="badge bg-slate-100 text-slate-600">Archived</span>
      )}
      <label className="block">
        <span className="text-xs text-slate-500">Name</span>
        <input
          className="input"
          value={ed.name}
          disabled={!warehouse.is_active}
          onChange={(e) => ed.setName(e.target.value)}
        />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="text-xs text-slate-500">Min inventory</span>
          <input
            type="number"
            className="input"
            min={0}
            value={ed.minInv}
            disabled={!warehouse.is_active}
            onChange={(e) => ed.setMinInv(Number(e.target.value))}
          />
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Max capacity</span>
          <input
            type="number"
            className="input"
            min={1}
            value={ed.maxCap}
            disabled={!warehouse.is_active}
            onChange={(e) => ed.setMaxCap(Number(e.target.value))}
          />
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Min pages / day</span>
          <input
            type="number"
            className="input"
            min={1}
            step={1}
            value={ed.minPages}
            placeholder="Not set"
            disabled={!warehouse.is_active}
            onChange={(e) => ed.setMinPages(e.target.value)}
          />
        </label>
      </div>
      {ed.error && <p className="text-xs text-rose-600">{ed.error}</p>}
      {lifecycle.error && (
        <p className="text-xs text-rose-600">{lifecycle.error}</p>
      )}
      <button
        type="button"
        className="btn-primary w-full"
        disabled={!warehouse.is_active || !ed.dirty || ed.pending}
        onClick={() => void ed.save()}
      >
        {ed.pending ? "Saving..." : "Save"}
      </button>
      <button
        type="button"
        className="btn-secondary w-full"
        disabled={lifecycle.pending}
        onClick={() =>
          void lifecycle.run(warehouse.is_active ? "archive" : "restore")
        }
      >
        {warehouse.is_active ? (
          <Trash2 className="h-4 w-4" />
        ) : (
          <Undo2 className="h-4 w-4" />
        )}
        {lifecycle.pending
          ? "Working..."
          : warehouse.is_active
            ? "Archive"
            : "Restore"}
      </button>
    </div>
  );
}

function UsersSection() {
  const { data } = useUsers();
  const { data: warehouses } = useWarehouses();
  const update = useUpdateUser();

  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <h2 className="font-semibold">Users</h2>
        <p className="text-xs text-slate-500">
          Roles default to those granted via Entra App Roles. Pinning a role
          here will override Entra until you clear it. New users start with no
          warehouse access — toggle the checkboxes to grant per-warehouse
          permissions.
        </p>
      </header>
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
            <tr>
              <th className="px-4 py-2.5 text-left">User</th>
              <th className="px-4 py-2.5 text-left">Role</th>
              <th className="px-4 py-2.5 text-left">Override</th>
              <th className="px-4 py-2.5 text-left">Active</th>
              <th className="px-4 py-2.5 text-left">Email alerts</th>
              <th className="px-4 py-2.5 text-left">Warehouses</th>
              <th className="px-4 py-2.5 text-left">Last login</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data?.map((u) => (
              <tr key={u.id} className="hover:bg-slate-50">
                <td className="px-4 py-2.5">
                  <div className="font-medium">
                    {u.display_name || u.email}
                  </div>
                  <div className="text-xs text-slate-500">{u.email}</div>
                </td>
                <td className="px-4 py-2.5">
                  <select
                    className="input inline-block w-auto"
                    value={u.role}
                    onChange={(e) =>
                      update.mutate({
                        id: u.id,
                        patch: {
                          role: e.target.value as Role,
                          role_override: true,
                        },
                      })
                    }
                  >
                    <option value="viewer">Viewer</option>
                    <option value="warehouse_mover">Warehouse mover</option>
                    <option value="operator">Operator</option>
                    <option value="admin">Admin</option>
                  </select>
                </td>
                <td className="px-4 py-2.5">
                  <input
                    type="checkbox"
                    checked={u.role_override}
                    onChange={(e) =>
                      update.mutate({
                        id: u.id,
                        patch: { role_override: e.target.checked },
                      })
                    }
                  />
                </td>
                <td className="px-4 py-2.5">
                  <input
                    type="checkbox"
                    checked={u.is_active}
                    onChange={(e) =>
                      update.mutate({
                        id: u.id,
                        patch: { is_active: e.target.checked },
                      })
                    }
                  />
                </td>
                <td className="px-4 py-2.5">
                  <input
                    type="checkbox"
                    checked={u.email_alerts_enabled}
                    onChange={(e) =>
                      update.mutate({
                        id: u.id,
                        patch: { email_alerts_enabled: e.target.checked },
                      })
                    }
                    title={
                      u.email_alerts_enabled
                        ? "Receives alert emails for accessible warehouses"
                        : "Opted out of alert emails"
                    }
                  />
                </td>
                <td className="px-4 py-2.5">
                  <WarehouseAccessCell
                    user={u}
                    warehouses={warehouses ?? []}
                    onChange={(warehouse_ids) =>
                      update.mutate({ id: u.id, patch: { warehouse_ids } })
                    }
                  />
                </td>
                <td className="px-4 py-2.5 text-slate-500">
                  {u.last_login_at
                    ? new Date(u.last_login_at).toLocaleString()
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="divide-y divide-slate-100 md:hidden">
        {data?.map((u) => (
          <UserCard
            key={u.id}
            user={u}
            warehouses={warehouses ?? []}
            onPatch={(patch) => update.mutate({ id: u.id, patch })}
          />
        ))}
      </div>
    </section>
  );
}

function UserCard({
  user,
  warehouses,
  onPatch,
}: {
  user: User;
  warehouses: Warehouse[];
  onPatch: (patch: {
    role?: Role;
    role_override?: boolean;
    is_active?: boolean;
    email_alerts_enabled?: boolean;
    warehouse_ids?: number[];
  }) => void;
}) {
  return (
    <div className="space-y-3 px-4 py-3">
      <div>
        <div className="text-sm font-medium">
          {user.display_name || user.email}
        </div>
        <div className="text-xs text-slate-500">{user.email}</div>
      </div>
      <label className="block">
        <span className="text-xs text-slate-500">Role</span>
        <select
          className="input"
          value={user.role}
          onChange={(e) =>
            onPatch({ role: e.target.value as Role, role_override: true })
          }
        >
          <option value="viewer">Viewer</option>
          <option value="warehouse_mover">Warehouse mover</option>
          <option value="operator">Operator</option>
          <option value="admin">Admin</option>
        </select>
      </label>
      <div className="grid grid-cols-1 gap-2 text-sm">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={user.role_override}
            onChange={(e) => onPatch({ role_override: e.target.checked })}
          />
          <span>Override Entra role</span>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={user.is_active}
            onChange={(e) => onPatch({ is_active: e.target.checked })}
          />
          <span>Active</span>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={user.email_alerts_enabled}
            onChange={(e) =>
              onPatch({ email_alerts_enabled: e.target.checked })
            }
          />
          <span>Email alerts</span>
        </label>
      </div>
      <div>
        <div className="mb-1 text-xs text-slate-500">Warehouses</div>
        <WarehouseAccessCell
          user={user}
          warehouses={warehouses}
          onChange={(warehouse_ids) => onPatch({ warehouse_ids })}
        />
      </div>
      <div className="text-xs text-slate-500">
        Last login{" "}
        {user.last_login_at
          ? new Date(user.last_login_at).toLocaleString()
          : "—"}
      </div>
    </div>
  );
}

function RecipientsPreview() {
  const { data, isLoading, error } = useAlertRecipients();

  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <div className="flex items-center gap-2">
          <Mail className="h-4 w-4 text-slate-500" />
          <h2 className="font-semibold">Alert email routing</h2>
        </div>
        <p className="text-xs text-slate-500">
          Who would receive an alert email if one fired right now. Active
          users with the warehouse ACL granted (plus all admins) get the
          primary mail; admins always cover escalation. Toggle "Email
          alerts" off above to opt a user out without removing their access.
        </p>
      </header>
      {isLoading && (
        <div className="px-5 py-6 text-sm text-slate-400">Loading…</div>
      )}
      {error && (
        <div className="px-5 py-6 text-sm text-rose-600">
          Failed to load recipient preview.
        </div>
      )}
      {data && (
        <div className="divide-y divide-slate-100">
          {data.warehouses.map((w) => (
            <div
              key={w.warehouse_id}
              className="grid gap-3 px-5 py-3 sm:grid-cols-[14rem_1fr]"
            >
              <div className="text-sm font-medium text-slate-700">
                {w.warehouse_name}
              </div>
              <RecipientChips emails={w.primary} emptyHint="No recipients (alerts will fall back to ALERT_EMAIL_TO)" />
            </div>
          ))}
          <div className="grid gap-3 bg-slate-50/60 px-5 py-3 sm:grid-cols-[14rem_1fr]">
            <div className="text-sm font-medium text-slate-700">
              Escalation (admins)
            </div>
            <RecipientChips
              emails={data.escalation}
              emptyHint="No active admins with email alerts enabled"
            />
          </div>
        </div>
      )}
    </section>
  );
}

function RecipientChips({
  emails,
  emptyHint,
}: {
  emails: string[];
  emptyHint: string;
}) {
  if (emails.length === 0) {
    return <span className="text-xs italic text-slate-400">{emptyHint}</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {emails.map((email) => (
        <span
          key={email}
          className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-700"
        >
          {email}
        </span>
      ))}
    </div>
  );
}

function WarehouseAccessCell({
  user,
  warehouses,
  onChange,
}: {
  user: User;
  warehouses: Warehouse[];
  onChange: (warehouse_ids: number[]) => void;
}) {
  // Admins always have unrestricted access -- the ACL doesn't apply to them
  // regardless of what's stored in user_warehouse_access. Surface that as a
  // read-only badge so it's clear to the operator.
  if (user.role === "admin") {
    return (
      <span className="inline-flex items-center rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
        All warehouses
      </span>
    );
  }
  const granted = new Set(user.warehouse_ids);
  return (
    <div className="flex flex-wrap gap-2">
      {warehouses.length === 0 ? (
        <span className="text-xs text-slate-400">No warehouses</span>
      ) : (
        warehouses.map((w) => {
          const checked = granted.has(w.id);
          return (
            <label
              key={w.id}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs hover:bg-slate-50"
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => {
                  const next = new Set(granted);
                  if (e.target.checked) {
                    next.add(w.id);
                  } else {
                    next.delete(w.id);
                  }
                  onChange(Array.from(next).sort((a, b) => a - b));
                }}
              />
              <span>{w.name}</span>
            </label>
          );
        })
      )}
    </div>
  );
}
