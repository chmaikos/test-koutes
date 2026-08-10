import { type ReactNode, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
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
  useMe,
  useRestoreWarehouse,
  useUpdateEmployee,
  useUpdateMyRequestEmailPreference,
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
  const me = useMe();
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-slate-500">
          Personal notifications and administrative warehouse configuration.
        </p>
      </header>

      <PersonalNotificationsSection />
      {me.data?.role === "admin" && (
        <>
          <WarehousesSection />
          <EmployeesSection />
          <UsersSection />
          <RecipientsPreview />
        </>
      )}
    </div>
  );
}

function PersonalNotificationsSection() {
  const me = useMe();
  const update = useUpdateMyRequestEmailPreference();
  return (
    <section className="card card-pad">
      <h2 className="font-semibold">Request notifications</h2>
      <p className="mt-1 text-xs text-slate-500">
        In-app notifications always stay enabled. Email can be switched off
        independently from warehouse alert email.
      </p>
      <label className="mt-4 flex items-center gap-3 text-sm">
        <input
          type="checkbox"
          checked={me.data?.email_requests_enabled ?? true}
          disabled={!me.data || update.isPending}
          onChange={(event) => update.mutate(event.target.checked)}
        />
        Email me about request workflow activity
      </label>
    </section>
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
      <header className="flex flex-col gap-4 border-b border-slate-100 px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="font-semibold">Warehouses & thresholds</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Manage inventory, demand planning, productivity, and receipt
            controls for each warehouse.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 sm:justify-end">
          <label className="inline-flex min-h-11 items-center gap-2 text-sm text-slate-600 md:min-h-0">
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
      <div className="space-y-3 bg-slate-50/50 p-3 sm:p-5">
        {data?.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white px-5 py-8 text-center">
            <p className="text-sm font-medium text-slate-700">
              No warehouses to show
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Add a warehouse or include archived warehouses.
            </p>
          </div>
        )}
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
    lead_time_days: number;
    safety_stock_percent: number;
    history_30_weight: number;
    history_90_weight: number;
    forecast_adjustment: number | null;
    receipt_mode: Warehouse["receipt_mode"];
    require_erp_document: boolean;
    quarantine_imports: boolean;
    quarantine_manual_receipts: boolean;
    two_person_approval_threshold: number | null;
  }) => Promise<unknown>;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [minInv, setMinInv] = useState(0);
  const [maxCap, setMaxCap] = useState(1000);
  const [minPages, setMinPages] = useState("");
  const [leadTime, setLeadTime] = useState(0);
  const [safetyStock, setSafetyStock] = useState(0);
  const [history30Weight, setHistory30Weight] = useState(70);
  const [history90Weight, setHistory90Weight] = useState(30);
  const [forecastAdjustment, setForecastAdjustment] = useState("");
  const [receiptMode, setReceiptMode] =
    useState<Warehouse["receipt_mode"]>("auto_complete");
  const [requireDocument, setRequireDocument] = useState(false);
  const [quarantineImports, setQuarantineImports] = useState(false);
  const [quarantineManual, setQuarantineManual] = useState(false);
  const [approvalThreshold, setApprovalThreshold] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const validationError =
    inventorySettingsError(minInv, maxCap, minPages) ??
    planningSettingsError({
      leadTime,
      safetyStock,
      history30Weight,
      history90Weight,
      forecastAdjustment,
    }) ??
    approvalThresholdError(approvalThreshold);

  async function createWarehouse() {
    if (!name.trim() || validationError) return;
    setPending(true);
    setError(null);
    try {
      await onCreate({
        name: name.trim(),
        min_inventory: minInv,
        max_capacity: maxCap,
        min_pages_per_day: minPages === "" ? null : Number(minPages),
        lead_time_days: leadTime,
        safety_stock_percent: safetyStock,
        history_30_weight: history30Weight,
        history_90_weight: history90Weight,
        forecast_adjustment:
          forecastAdjustment === "" ? null : Number(forecastAdjustment),
        receipt_mode: receiptMode,
        require_erp_document: requireDocument,
        quarantine_imports: quarantineImports,
        quarantine_manual_receipts: quarantineManual,
        two_person_approval_threshold:
          approvalThreshold === "" ? null : Number(approvalThreshold),
      });
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Failed to create warehouse";
      setError(
        typeof detail === "string" ? detail : "Failed to create warehouse",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="border-b border-slate-200 bg-slate-50/80 px-4 py-5 sm:px-5"
      onSubmit={(event) => {
        event.preventDefault();
        void createWarehouse();
      }}
    >
      <div className="mb-5">
        <h3 className="font-semibold text-slate-900">Add a new warehouse</h3>
        <p className="mt-1 text-sm text-slate-500">
          Set the operating limits and policies now. You can update them later.
        </p>
      </div>
      <div className="space-y-4">
        <WarehouseEditorSection
          title="General & thresholds"
          description="Identify the warehouse and define its inventory and productivity guardrails."
        >
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <FieldLabel label="Warehouse name" className="sm:col-span-2">
              <input
                className="input"
                autoFocus
                value={name}
                placeholder="Building 4"
                onChange={(event) => setName(event.target.value)}
              />
            </FieldLabel>
            <FieldLabel label="Minimum inventory" hint="boxes">
              <input
                type="number"
                className="input"
                min={0}
                value={minInv}
                onChange={(event) => setMinInv(Number(event.target.value))}
              />
            </FieldLabel>
            <FieldLabel label="Maximum capacity" hint="boxes">
              <input
                type="number"
                className="input"
                min={1}
                value={maxCap}
                onChange={(event) => setMaxCap(Number(event.target.value))}
              />
            </FieldLabel>
            <FieldLabel
              label="Minimum productivity"
              hint="pages per day"
              className="sm:col-span-2 lg:col-span-1"
            >
              <input
                type="number"
                className="input"
                min={1}
                step={1}
                value={minPages}
                placeholder="Not set"
                onChange={(event) => setMinPages(event.target.value)}
              />
            </FieldLabel>
          </div>
        </WarehouseEditorSection>

        <WarehouseEditorSection
          title="Demand planning"
          description="Tune how recent demand, lead time, and safety stock shape inventory recommendations."
        >
          <PlanningInputs
            leadTime={leadTime}
            setLeadTime={setLeadTime}
            safetyStock={safetyStock}
            setSafetyStock={setSafetyStock}
            history30Weight={history30Weight}
            setHistory30Weight={setHistory30Weight}
            history90Weight={history90Weight}
            setHistory90Weight={setHistory90Weight}
            forecastAdjustment={forecastAdjustment}
            setForecastAdjustment={setForecastAdjustment}
          />
        </WarehouseEditorSection>

        <WarehouseEditorSection
          title="Receipt governance"
          description="Choose how incoming receipts are completed, reviewed, and quarantined."
        >
          <GovernanceInputs
            receiptMode={receiptMode}
            setReceiptMode={setReceiptMode}
            requireDocument={requireDocument}
            setRequireDocument={setRequireDocument}
            quarantineImports={quarantineImports}
            setQuarantineImports={setQuarantineImports}
            quarantineManual={quarantineManual}
            setQuarantineManual={setQuarantineManual}
            approvalThreshold={approvalThreshold}
            setApprovalThreshold={setApprovalThreshold}
            disabled={pending}
          />
        </WarehouseEditorSection>
      </div>

      <div className="mt-5 flex flex-col gap-3 border-t border-slate-200 pt-4 sm:flex-row sm:items-center sm:justify-between">
        <div aria-live="polite">
          {(error || validationError) && (
            <p className="text-sm text-rose-600" role="alert">
              {error || validationError}
            </p>
          )}
        </div>
        <div className="flex flex-col-reverse gap-2 sm:flex-row">
          <button
            type="button"
            className="btn-secondary"
            disabled={pending}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn-primary"
            disabled={pending || !name.trim() || validationError !== null}
          >
            {pending ? "Creating..." : "Create warehouse"}
          </button>
        </div>
      </div>
    </form>
  );
}

function PlanningInputs({
  leadTime,
  setLeadTime,
  safetyStock,
  setSafetyStock,
  history30Weight,
  setHistory30Weight,
  history90Weight,
  setHistory90Weight,
  forecastAdjustment,
  setForecastAdjustment,
  disabled = false,
}: {
  leadTime: number;
  setLeadTime: (value: number) => void;
  safetyStock: number;
  setSafetyStock: (value: number) => void;
  history30Weight: number;
  setHistory30Weight: (value: number) => void;
  history90Weight: number;
  setHistory90Weight: (value: number) => void;
  forecastAdjustment: string;
  setForecastAdjustment: (value: string) => void;
  disabled?: boolean;
}) {
  const validationError = planningSettingsError({
    leadTime,
    safetyStock,
    history30Weight,
    history90Weight,
    forecastAdjustment,
  });
  const normalizedWeights = normalizedWeightRatio(
    history30Weight,
    history90Weight,
  );
  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <FieldLabel label="Lead time" hint="days">
          <input
            className="input"
            type="number"
            min={0}
            max={365}
            value={leadTime}
            disabled={disabled}
            onChange={(event) => setLeadTime(Number(event.target.value))}
          />
        </FieldLabel>
        <FieldLabel label="Safety stock" hint="% of forecast demand">
          <input
            className="input"
            type="number"
            min={0}
            max={500}
            value={safetyStock}
            disabled={disabled}
            onChange={(event) => setSafetyStock(Number(event.target.value))}
          />
        </FieldLabel>
        <FieldLabel label="Forecast adjustment" hint="boxes">
          <input
            className="input"
            type="number"
            min={-5000}
            max={5000}
            value={forecastAdjustment}
            placeholder="None"
            disabled={disabled}
            onChange={(event) => setForecastAdjustment(event.target.value)}
          />
        </FieldLabel>
      </div>

      <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50/70 p-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
          <div>
            <h5 className="text-sm font-medium text-slate-800">
              History weighting
            </h5>
            <p className="text-xs text-slate-500">
              Balance recent demand against the longer trend.
            </p>
          </div>
          <span className="mt-1 inline-flex self-start rounded-full bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-700 sm:mt-0">
            Normalized ratio: {normalizedWeights}
          </span>
        </div>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <FieldLabel label="Recent history" hint="30-day weight">
            <input
              className="input"
              type="number"
              min={0}
              max={1000}
              value={history30Weight}
              disabled={disabled}
              onChange={(event) =>
                setHistory30Weight(Number(event.target.value))
              }
            />
          </FieldLabel>
          <FieldLabel label="Longer history" hint="90-day weight">
            <input
              className="input"
              type="number"
              min={0}
              max={1000}
              value={history90Weight}
              disabled={disabled}
              onChange={(event) =>
                setHistory90Weight(Number(event.target.value))
              }
            />
          </FieldLabel>
        </div>
      </div>

      <p
        className={`mt-3 text-xs leading-5 ${
          validationError ? "text-rose-600" : "text-slate-500"
        }`}
      >
        {validationError ??
          "Lead time drives forecast demand; safety stock is calculated from that demand, and the adjustment adds or removes boxes. With insufficient history, recommendations use the current minimum-gap formula."}
      </p>
    </div>
  );
}

type PlanningSettingsValues = {
  leadTime: number;
  safetyStock: number;
  history30Weight: number;
  history90Weight: number;
  forecastAdjustment: string;
};

function normalizedWeightRatio(
  history30Weight: number,
  history90Weight: number,
): string {
  const total = history30Weight + history90Weight;
  if (total <= 0) return "—";
  const recent = Math.round((history30Weight / total) * 100);
  return `${recent}/${100 - recent}`;
}

function planningSettingsError(values: PlanningSettingsValues): string | null {
  const {
    leadTime,
    safetyStock,
    history30Weight,
    history90Weight,
    forecastAdjustment,
  } = values;
  if (!Number.isInteger(leadTime) || leadTime < 0 || leadTime > 365) {
    return "Lead time must be a whole number from 0 to 365 days.";
  }
  if (
    !Number.isInteger(safetyStock) ||
    safetyStock < 0 ||
    safetyStock > 500
  ) {
    return "Safety stock must be a whole percentage from 0 to 500.";
  }
  if (
    !Number.isInteger(history30Weight) ||
    !Number.isInteger(history90Weight) ||
    history30Weight < 0 ||
    history30Weight > 1000 ||
    history90Weight < 0 ||
    history90Weight > 1000
  ) {
    return "History weights must be whole numbers from 0 to 1000.";
  }
  if (history30Weight + history90Weight <= 0) {
    return "At least one history weight must be greater than zero.";
  }
  if (forecastAdjustment !== "") {
    const adjustment = Number(forecastAdjustment);
    if (
      !Number.isInteger(adjustment) ||
      adjustment < -5000 ||
      adjustment > 5000
    ) {
      return "Forecast adjustment must be a whole number from -5000 to 5000.";
    }
  }
  return null;
}

function inventorySettingsError(
  minInventory: number,
  maxCapacity: number,
  minPages: string,
): string | null {
  if (!Number.isInteger(minInventory) || minInventory < 0) {
    return "Minimum inventory must be a non-negative whole number.";
  }
  if (!Number.isInteger(maxCapacity) || maxCapacity <= minInventory) {
    return "Maximum capacity must be a whole number above minimum inventory.";
  }
  if (
    minPages !== "" &&
    (!Number.isInteger(Number(minPages)) || Number(minPages) <= 0)
  ) {
    return "Minimum pages per day must be a positive whole number or blank.";
  }
  return null;
}

function approvalThresholdError(value: string): string | null {
  if (
    value !== "" &&
    (!Number.isInteger(Number(value)) || Number(value) <= 0)
  ) {
    return "Two-person approval threshold must be a positive whole number or blank.";
  }
  return null;
}

type WarehousePatch = {
  name?: string;
  min_inventory?: number;
  max_capacity?: number;
  min_pages_per_day?: number | null;
  lead_time_days?: number;
  safety_stock_percent?: number;
  history_30_weight?: number;
  history_90_weight?: number;
  forecast_adjustment?: number | null;
  receipt_mode?: Warehouse["receipt_mode"];
  require_erp_document?: boolean;
  quarantine_imports?: boolean;
  quarantine_manual_receipts?: boolean;
  two_person_approval_threshold?: number | null;
};

/** Keeps a warehouse card's draft, validation, and save state together. */
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
  const [leadTime, setLeadTime] = useState(warehouse.lead_time_days);
  const [safetyStock, setSafetyStock] = useState(
    warehouse.safety_stock_percent,
  );
  const [history30Weight, setHistory30Weight] = useState(
    warehouse.history_30_weight,
  );
  const [history90Weight, setHistory90Weight] = useState(
    warehouse.history_90_weight,
  );
  const [forecastAdjustment, setForecastAdjustment] = useState(
    warehouse.forecast_adjustment?.toString() ?? "",
  );
  const [receiptMode, setReceiptMode] = useState(warehouse.receipt_mode);
  const [requireDocument, setRequireDocument] = useState(
    warehouse.require_erp_document,
  );
  const [quarantineImports, setQuarantineImports] = useState(
    warehouse.quarantine_imports,
  );
  const [quarantineManual, setQuarantineManual] = useState(
    warehouse.quarantine_manual_receipts,
  );
  const [approvalThreshold, setApprovalThreshold] = useState(
    warehouse.two_person_approval_threshold?.toString() ?? "",
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const validationError =
    inventorySettingsError(minInv, maxCap, minPages) ??
    planningSettingsError({
      leadTime,
      safetyStock,
      history30Weight,
      history90Weight,
      forecastAdjustment,
    }) ??
    approvalThresholdError(approvalThreshold);

  const dirty =
    name !== warehouse.name ||
    minInv !== warehouse.min_inventory ||
    maxCap !== warehouse.max_capacity ||
    minPages !== (warehouse.min_pages_per_day?.toString() ?? "") ||
    leadTime !== warehouse.lead_time_days ||
    safetyStock !== warehouse.safety_stock_percent ||
    history30Weight !== warehouse.history_30_weight ||
    history90Weight !== warehouse.history_90_weight ||
    forecastAdjustment !== (warehouse.forecast_adjustment?.toString() ?? "") ||
    receiptMode !== warehouse.receipt_mode ||
    requireDocument !== warehouse.require_erp_document ||
    quarantineImports !== warehouse.quarantine_imports ||
    quarantineManual !== warehouse.quarantine_manual_receipts ||
    approvalThreshold !==
      (warehouse.two_person_approval_threshold?.toString() ?? "");

  async function save() {
    if (validationError) {
      setError(validationError);
      return;
    }
    setPending(true);
    setError(null);
    try {
      await onSave({
        name,
        min_inventory: minInv,
        max_capacity: maxCap,
        min_pages_per_day: minPages === "" ? null : Number(minPages),
        lead_time_days: leadTime,
        safety_stock_percent: safetyStock,
        history_30_weight: history30Weight,
        history_90_weight: history90Weight,
        forecast_adjustment:
          forecastAdjustment === "" ? null : Number(forecastAdjustment),
        receipt_mode: receiptMode,
        require_erp_document: requireDocument,
        quarantine_imports: quarantineImports,
        quarantine_manual_receipts: quarantineManual,
        two_person_approval_threshold:
          approvalThreshold === "" ? null : Number(approvalThreshold),
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
    leadTime,
    setLeadTime,
    safetyStock,
    setSafetyStock,
    history30Weight,
    setHistory30Weight,
    history90Weight,
    setHistory90Weight,
    forecastAdjustment,
    setForecastAdjustment,
    receiptMode,
    setReceiptMode,
    requireDocument,
    setRequireDocument,
    quarantineImports,
    setQuarantineImports,
    quarantineManual,
    setQuarantineManual,
    approvalThreshold,
    setApprovalThreshold,
    pending,
    error,
    validationError,
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

function WarehouseEditorSection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
      <div className="mb-4">
        <h4 className="text-sm font-semibold text-slate-900">{title}</h4>
        <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
      </div>
      {children}
    </section>
  );
}

function FieldLabel({
  label,
  hint,
  className = "",
  children,
}: {
  label: string;
  hint?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1.5 flex flex-wrap items-baseline justify-between gap-x-2 text-sm font-medium text-slate-700">
        {label}
        {hint && (
          <span className="text-xs font-normal text-slate-400">{hint}</span>
        )}
      </span>
      {children}
    </label>
  );
}

function ToggleRow({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label
      className={`flex items-start justify-between gap-4 rounded-lg border border-slate-200 bg-white p-3.5 transition ${
        disabled
          ? "cursor-not-allowed opacity-60"
          : "cursor-pointer hover:border-slate-300 hover:bg-slate-50"
      }`}
    >
      <span>
        <span className="block text-sm font-medium text-slate-800">{label}</span>
        <span className="mt-0.5 block text-xs leading-5 text-slate-500">
          {description}
        </span>
      </span>
      <input
        type="checkbox"
        role="switch"
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span
        aria-hidden="true"
        className={`mt-0.5 inline-flex h-6 w-11 shrink-0 rounded-full p-0.5 transition peer-focus-visible:ring-2 peer-focus-visible:ring-brand-500 peer-focus-visible:ring-offset-2 ${
          checked ? "bg-brand-600" : "bg-slate-300"
        }`}
      >
        <span
          className={`h-5 w-5 rounded-full bg-white shadow-sm transition ${
            checked ? "translate-x-5" : "translate-x-0"
          }`}
        />
      </span>
    </label>
  );
}

function GovernanceInputs({
  receiptMode,
  setReceiptMode,
  requireDocument,
  setRequireDocument,
  quarantineImports,
  setQuarantineImports,
  quarantineManual,
  setQuarantineManual,
  approvalThreshold,
  setApprovalThreshold,
  disabled,
}: {
  receiptMode: Warehouse["receipt_mode"];
  setReceiptMode: (value: Warehouse["receipt_mode"]) => void;
  requireDocument: boolean;
  setRequireDocument: (value: boolean) => void;
  quarantineImports: boolean;
  setQuarantineImports: (value: boolean) => void;
  quarantineManual: boolean;
  setQuarantineManual: (value: boolean) => void;
  approvalThreshold: string;
  setApprovalThreshold: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2">
        <FieldLabel label="Receipt workflow">
          <select
            className="input"
            value={receiptMode}
            disabled={disabled}
            onChange={(event) =>
              setReceiptMode(
                event.target.value as Warehouse["receipt_mode"],
              )
            }
          >
            <option value="auto_complete">Complete automatically</option>
            <option value="admin_review">Require admin review</option>
          </select>
          <span className="mt-1.5 block text-xs leading-5 text-slate-500">
            {receiptMode === "auto_complete"
              ? "Valid receipts enter inventory without an approval step."
              : "An administrator must approve receipts before completion."}
          </span>
        </FieldLabel>
        <FieldLabel label="Two-person approval threshold" hint="boxes">
          <input
            type="number"
            min={1}
            step={1}
            className="input"
            value={approvalThreshold}
            placeholder="Not set"
            disabled={disabled}
            onChange={(event) => setApprovalThreshold(event.target.value)}
          />
          <span className="mt-1.5 block text-xs leading-5 text-slate-500">
            Require a second approver when a receipt reaches this size.
          </span>
        </FieldLabel>
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <ToggleRow
          label="Require ERP delivery note"
          description="Receipts must include the matching ERP document reference."
          checked={requireDocument}
          disabled={disabled}
          onChange={setRequireDocument}
        />
        <ToggleRow
          label="Quarantine spreadsheet imports"
          description="Hold receipts created from XLSX imports for review."
          checked={quarantineImports}
          disabled={disabled}
          onChange={setQuarantineImports}
        />
        <ToggleRow
          label="Quarantine manual receipts"
          description="Hold receipts entered by a user for review before release."
          checked={quarantineManual}
          disabled={disabled}
          onChange={setQuarantineManual}
        />
      </div>
    </div>
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
  const [expanded, setExpanded] = useState(false);
  const ed = useWarehouseEditor(warehouse, onSave);
  const lifecycle = useWarehouseLifecycle(warehouse, onArchive, onRestore);
  const editorId = `warehouse-editor-${warehouse.id}`;
  const quarantineRuleCount = [
    ed.quarantineImports,
    ed.quarantineManual,
  ].filter(Boolean).length;
  const receiptSummary = [
    ed.receiptMode === "auto_complete" ? "Auto-complete" : "Admin review",
    ed.requireDocument ? "ERP note required" : null,
    quarantineRuleCount > 0
      ? `${quarantineRuleCount} quarantine ${
          quarantineRuleCount === 1 ? "rule" : "rules"
        }`
      : null,
    ed.approvalThreshold ? `2-person at ${ed.approvalThreshold}+` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <article
      className={`overflow-hidden rounded-xl border bg-white shadow-sm transition ${
        expanded ? "border-brand-200 ring-1 ring-brand-100" : "border-slate-200"
      }`}
    >
      <button
        type="button"
        className="w-full px-4 py-4 text-left transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 sm:px-5"
        aria-expanded={expanded}
        aria-controls={editorId}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="flex items-start justify-between gap-4">
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-2">
              <span className="truncate text-base font-semibold text-slate-900">
                {ed.name || "Unnamed warehouse"}
              </span>
              <span
                className={`badge ${
                  warehouse.is_active
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-slate-100 text-slate-600"
                }`}
              >
                {warehouse.is_active ? "Active" : "Archived"}
              </span>
              {ed.dirty && warehouse.is_active && (
                <span className="badge bg-amber-50 text-amber-700">
                  Unsaved changes
                </span>
              )}
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-2 text-sm font-medium text-brand-700">
            {expanded
              ? "Close"
              : warehouse.is_active
                ? "Manage"
                : "View"}
            <ChevronDown
              className={`h-4 w-4 transition-transform ${
                expanded ? "rotate-180" : ""
              }`}
              aria-hidden="true"
            />
          </span>
        </span>

        <span className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 lg:grid-cols-4">
          <WarehouseSummaryItem
            label="Inventory limits"
            value={`${ed.minInv}–${ed.maxCap} boxes`}
          />
          <WarehouseSummaryItem
            label="Productivity"
            value={
              ed.minPages === ""
                ? "No minimum set"
                : `${ed.minPages} pages / day`
            }
          />
          <WarehouseSummaryItem
            label="Demand planning"
            value={`${ed.leadTime}d lead · ${normalizedWeightRatio(
              ed.history30Weight,
              ed.history90Weight,
            )} history`}
          />
          <WarehouseSummaryItem
            label="Receipt policy"
            value={receiptSummary}
          />
        </span>
      </button>

      {expanded && (
        <div
          id={editorId}
          className="border-t border-slate-200 bg-slate-50/60 p-3 sm:p-5"
        >
          {!warehouse.is_active && (
            <div className="mb-4 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
              This warehouse is archived. Its settings are read-only until it
              is restored.
            </div>
          )}

          <div className="space-y-4">
            <WarehouseEditorSection
              title="General & thresholds"
              description="Update the warehouse name, inventory limits, and minimum expected productivity."
            >
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <FieldLabel
                  label="Warehouse name"
                  className="sm:col-span-2"
                >
                  <input
                    className="input"
                    value={ed.name}
                    disabled={!warehouse.is_active}
                    onChange={(event) => ed.setName(event.target.value)}
                  />
                </FieldLabel>
                <FieldLabel label="Minimum inventory" hint="boxes">
                  <input
                    type="number"
                    className="input"
                    min={0}
                    value={ed.minInv}
                    disabled={!warehouse.is_active}
                    onChange={(event) =>
                      ed.setMinInv(Number(event.target.value))
                    }
                  />
                </FieldLabel>
                <FieldLabel label="Maximum capacity" hint="boxes">
                  <input
                    type="number"
                    className="input"
                    min={1}
                    value={ed.maxCap}
                    disabled={!warehouse.is_active}
                    onChange={(event) =>
                      ed.setMaxCap(Number(event.target.value))
                    }
                  />
                </FieldLabel>
                <FieldLabel
                  label="Minimum productivity"
                  hint="pages per day"
                  className="sm:col-span-2 lg:col-span-1"
                >
                  <input
                    type="number"
                    className="input"
                    min={1}
                    step={1}
                    value={ed.minPages}
                    placeholder="Not set"
                    disabled={!warehouse.is_active}
                    onChange={(event) => ed.setMinPages(event.target.value)}
                  />
                </FieldLabel>
              </div>
            </WarehouseEditorSection>

            <WarehouseEditorSection
              title="Demand planning"
              description="Tune how recent demand, lead time, and safety stock shape inventory recommendations."
            >
              <PlanningInputs
                disabled={!warehouse.is_active}
                leadTime={ed.leadTime}
                setLeadTime={ed.setLeadTime}
                safetyStock={ed.safetyStock}
                setSafetyStock={ed.setSafetyStock}
                history30Weight={ed.history30Weight}
                setHistory30Weight={ed.setHistory30Weight}
                history90Weight={ed.history90Weight}
                setHistory90Weight={ed.setHistory90Weight}
                forecastAdjustment={ed.forecastAdjustment}
                setForecastAdjustment={ed.setForecastAdjustment}
              />
            </WarehouseEditorSection>

            <WarehouseEditorSection
              title="Receipt governance"
              description="Control how incoming receipts are completed, reviewed, and quarantined."
            >
              <GovernanceInputs
                receiptMode={ed.receiptMode}
                setReceiptMode={ed.setReceiptMode}
                requireDocument={ed.requireDocument}
                setRequireDocument={ed.setRequireDocument}
                quarantineImports={ed.quarantineImports}
                setQuarantineImports={ed.setQuarantineImports}
                quarantineManual={ed.quarantineManual}
                setQuarantineManual={ed.setQuarantineManual}
                approvalThreshold={ed.approvalThreshold}
                setApprovalThreshold={ed.setApprovalThreshold}
                disabled={!warehouse.is_active}
              />
            </WarehouseEditorSection>
          </div>

          <div className="mt-5 flex flex-col gap-4 border-t border-slate-200 pt-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-h-5 text-sm" aria-live="polite">
              {ed.error || lifecycle.error || ed.validationError ? (
                <p className="text-rose-600" role="alert">
                  {ed.error || lifecycle.error || ed.validationError}
                </p>
              ) : ed.dirty ? (
                <p className="font-medium text-amber-700">
                  You have unsaved changes.
                </p>
              ) : (
                <p className="text-slate-500">
                  {warehouse.is_active
                    ? "All changes saved."
                    : "Archived settings are read-only."}
                </p>
              )}
            </div>
            <div className="flex flex-col-reverse gap-2 sm:flex-row">
              <button
                type="button"
                className={`btn-secondary ${
                  warehouse.is_active
                    ? "text-rose-700 hover:bg-rose-50"
                    : "text-emerald-700 hover:bg-emerald-50"
                }`}
                disabled={lifecycle.pending || ed.pending}
                onClick={() =>
                  void lifecycle.run(
                    warehouse.is_active ? "archive" : "restore",
                  )
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
                    ? "Archive warehouse"
                    : "Restore warehouse"}
              </button>
              {warehouse.is_active && (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={
                    !ed.dirty ||
                    ed.pending ||
                    lifecycle.pending ||
                    ed.validationError !== null
                  }
                  onClick={() => void ed.save()}
                >
                  {ed.pending
                    ? "Saving..."
                    : ed.validationError
                      ? "Fix errors to save"
                      : ed.dirty
                        ? "Save changes"
                        : "Saved"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </article>
  );
}

function WarehouseSummaryItem({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <span className="min-w-0">
      <span className="block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </span>
      <span className="mt-0.5 block truncate text-sm text-slate-700">
        {value}
      </span>
    </span>
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
              <th className="px-4 py-2.5 text-left">Request email</th>
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
                  <input
                    type="checkbox"
                    checked={u.email_requests_enabled}
                    onChange={(e) =>
                      update.mutate({
                        id: u.id,
                        patch: { email_requests_enabled: e.target.checked },
                      })
                    }
                    title="Receives request workflow email"
                  />
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
    email_requests_enabled?: boolean;
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
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={user.email_requests_enabled}
            onChange={(e) =>
              onPatch({ email_requests_enabled: e.target.checked })
            }
          />
          <span>Request email</span>
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
