import { useMemo, useState } from "react";
import clsx from "clsx";
import { Activity, Plus, Trash2 } from "lucide-react";
import { useHasRole } from "@/components/RoleGate";
import {
  useDeleteProductivityEntry,
  useEmployees,
  useProductivityDailySummary,
  useProductivityEntries,
  useProductivityWeeklySummary,
  useUpsertProductivityEntry,
  useWarehouses,
} from "@/api/hooks";
import type {
  Employee,
  Performer,
  ProductivityEntry,
  ProductivitySummary,
  Warehouse,
  WarehouseProductivity,
} from "@/api/types";

type Tab = "today" | "week";

/** Today's date as YYYY-MM-DD in the user's local time. */
function todayStr(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** Monday of the ISO week containing ``date``, formatted YYYY-MM-DD. */
function isoWeekStart(date: string): string {
  const d = new Date(`${date}T00:00:00`);
  const dow = (d.getDay() + 6) % 7; // 0 = Mon, 6 = Sun
  d.setDate(d.getDate() - dow);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function ProductivityPage() {
  const { data: warehouses } = useWarehouses();
  const [tab, setTab] = useState<Tab>("today");
  const [date, setDate] = useState<string>(todayStr());
  const canWrite = useHasRole(["admin", "operator"]);

  const dailyQuery = useProductivityDailySummary(undefined, date);
  const weeklyQuery = useProductivityWeeklySummary(
    undefined,
    isoWeekStart(date),
  );

  const summary = tab === "today" ? dailyQuery.data : weeklyQuery.data;
  const isLoading = tab === "today" ? dailyQuery.isLoading : weeklyQuery.isLoading;

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Productivity</h1>
          <p className="text-sm text-slate-500">
            Daily pages-per-hour by warehouse and employee.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="date"
            className="input max-w-[180px]"
            value={date}
            onChange={(e) => setDate(e.target.value || todayStr())}
            aria-label="Reporting date"
          />
        </div>
      </header>

      <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-1 text-sm font-medium">
        <TabButton active={tab === "today"} onClick={() => setTab("today")}>
          Today
        </TabButton>
        <TabButton active={tab === "week"} onClick={() => setTab("week")}>
          This week
        </TabButton>
      </div>

      <GrandTotalsCard summary={summary} isLoading={isLoading} />

      {!warehouses ? (
        <p className="text-sm text-slate-500">Loading warehouses...</p>
      ) : warehouses.length === 0 ? (
        <p className="text-sm text-slate-500">
          No warehouses are accessible to you.
        </p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-2">
          {warehouses.map((w) => (
            <WarehouseProductivityCard
              key={w.id}
              warehouse={w}
              summary={summary?.warehouses.find(
                (ws) => ws.warehouse_id === w.id,
              )}
              date={date}
              tab={tab}
              canWrite={canWrite}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "flex-1 rounded-md px-3 py-1.5 transition sm:flex-none",
        active ? "bg-white text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900",
      )}
    >
      {children}
    </button>
  );
}

function GrandTotalsCard({
  summary,
  isLoading,
}: {
  summary: ProductivitySummary | undefined;
  isLoading: boolean;
}) {
  if (isLoading || !summary) {
    return (
      <section className="card card-pad">
        <p className="text-sm text-slate-500">Loading totals...</p>
      </section>
    );
  }
  return (
    <section className="card card-pad">
      <header className="mb-3 flex items-center gap-2">
        <Activity className="h-4 w-4 text-brand-600" />
        <h2 className="font-semibold">
          Totals{" "}
          <span className="text-xs font-normal text-slate-500">
            {summary.period_start}
            {summary.period_start !== summary.period_end &&
              ` -> ${summary.period_end}`}
          </span>
        </h2>
      </header>
      <dl className="grid grid-cols-3 gap-3 text-sm">
        <Metric label="Pages" value={summary.total_pages.toString()} />
        <Metric label="Hours" value={summary.total_hours.toFixed(2)} />
        <Metric
          label="Pages / hr"
          value={summary.avg_pages_per_hour.toFixed(2)}
        />
      </dl>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-xl font-semibold text-slate-900">{value}</dd>
    </div>
  );
}

function WarehouseProductivityCard({
  warehouse,
  summary,
  date,
  tab,
  canWrite,
}: {
  warehouse: Warehouse;
  summary: WarehouseProductivity | undefined;
  date: string;
  tab: Tab;
  canWrite: boolean;
}) {
  const employees = useEmployees(warehouse.id, false);
  const [showAdd, setShowAdd] = useState(false);

  return (
    <section className="card overflow-hidden">
      <header className="border-b border-slate-100 px-5 py-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold">{warehouse.name}</h3>
            <p className="text-xs text-slate-500">
              {summary
                ? `${summary.entry_count} entr${
                    summary.entry_count === 1 ? "y" : "ies"
                  } from ${summary.active_employees} active employee${
                    summary.active_employees === 1 ? "" : "s"
                  }`
                : "No entries"}
            </p>
          </div>
          {canWrite && tab === "today" && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setShowAdd((v) => !v)}
            >
              <Plus className="h-4 w-4" />
              {showAdd ? "Cancel" : "Add entry"}
            </button>
          )}
        </div>
      </header>

      <div className="grid grid-cols-3 gap-3 px-5 py-4 text-sm">
        <Metric label="Pages" value={(summary?.total_pages ?? 0).toString()} />
        <Metric label="Hours" value={(summary?.total_hours ?? 0).toFixed(2)} />
        <Metric
          label="Pages / hr"
          value={(summary?.avg_pages_per_hour ?? 0).toFixed(2)}
        />
      </div>

      {showAdd && employees.data && (
        <NewEntryForm
          employees={employees.data}
          date={date}
          onClose={() => setShowAdd(false)}
        />
      )}

      <PerformersTable
        title="Top performers"
        performers={summary?.top ?? []}
        emptyHint="No data yet."
      />
      <PerformersTable
        title="Bottom performers"
        performers={summary?.bottom ?? []}
        emptyHint=""
      />

      {tab === "today" && (
        <EntriesList warehouseId={warehouse.id} date={date} canWrite={canWrite} />
      )}
    </section>
  );
}

function PerformersTable({
  title,
  performers,
  emptyHint,
}: {
  title: string;
  performers: Performer[];
  emptyHint: string;
}) {
  if (performers.length === 0) {
    if (!emptyHint) return null;
    return (
      <div className="border-t border-slate-100 px-5 py-3 text-xs text-slate-400">
        {title} — {emptyHint}
      </div>
    );
  }
  return (
    <div className="border-t border-slate-100 px-5 py-3">
      <div className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
        {title}
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-slate-100">
          {performers.map((p) => (
            <tr key={p.employee_id}>
              <td className="py-1.5 pr-2">{p.employee_name}</td>
              <td className="py-1.5 pr-2 text-right text-xs text-slate-500">
                {p.pages} pages · {p.hours.toFixed(2)}h
              </td>
              <td className="py-1.5 text-right font-semibold tabular-nums">
                {p.pages_per_hour.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NewEntryForm({
  employees,
  date,
  onClose,
}: {
  employees: Employee[];
  date: string;
  onClose: () => void;
}) {
  const upsert = useUpsertProductivityEntry();
  const [employeeId, setEmployeeId] = useState<number | null>(
    employees[0]?.id ?? null,
  );
  const [pages, setPages] = useState<number>(0);
  const [hours, setHours] = useState<number>(8);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => employees.find((e) => e.id === employeeId) ?? null,
    [employees, employeeId],
  );

  // Pre-fill the hours field with the employee's admin-set default so the
  // common case (a full standard shift) is one less number to type.
  const handleSelectEmployee = (id: number) => {
    setEmployeeId(id);
    const emp = employees.find((e) => e.id === id);
    if (emp) {
      const parsed = Number(emp.default_hours_per_day);
      if (!Number.isNaN(parsed) && parsed > 0) {
        setHours(parsed);
      }
    }
  };

  if (employees.length === 0) {
    return (
      <div className="border-t border-slate-100 bg-slate-50/60 px-5 py-3 text-sm text-slate-500">
        No active employees in this warehouse. Add one in{" "}
        <a className="underline" href="/settings">
          Settings
        </a>
        .
      </div>
    );
  }

  return (
    <div className="grid gap-3 border-t border-slate-100 bg-slate-50/60 px-5 py-4 sm:grid-cols-[1.5fr_1fr_1fr_auto] sm:items-end">
      <label className="block">
        <span className="text-xs text-slate-500">Employee</span>
        <select
          className="input"
          value={employeeId ?? ""}
          onChange={(e) => handleSelectEmployee(Number(e.target.value))}
        >
          {employees.map((e) => (
            <option key={e.id} value={e.id}>
              {e.full_name}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Pages</span>
        <input
          type="number"
          className="input"
          min={0}
          value={pages}
          onChange={(e) => setPages(Math.max(0, Number(e.target.value)))}
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500">Hours</span>
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
          disabled={!selected || hours <= 0 || upsert.isPending}
          onClick={async () => {
            if (!selected) return;
            setError(null);
            try {
              await upsert.mutateAsync({
                employee_id: selected.id,
                entry_date: date,
                pages,
                hours_worked: hours,
                note: note || undefined,
              });
              onClose();
            } catch (err: unknown) {
              const detail =
                (err as { response?: { data?: { detail?: string } } })?.response
                  ?.data?.detail ?? "Failed to save entry";
              setError(typeof detail === "string" ? detail : "Failed to save entry");
            }
          }}
        >
          {upsert.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      <label className="block sm:col-span-4">
        <span className="text-xs text-slate-500">Note (optional)</span>
        <input
          className="input"
          value={note}
          maxLength={500}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      {error && <p className="text-xs text-rose-600 sm:col-span-4">{error}</p>}
    </div>
  );
}

function EntriesList({
  warehouseId,
  date,
  canWrite,
}: {
  warehouseId: number;
  date: string;
  canWrite: boolean;
}) {
  const entries = useProductivityEntries({
    warehouse_id: warehouseId,
    from_date: date,
    to_date: date,
  });
  const remove = useDeleteProductivityEntry();
  const employees = useEmployees(warehouseId, true);
  const employeeName = (id: number) =>
    employees.data?.find((e) => e.id === id)?.full_name ?? `#${id}`;

  if (entries.isLoading) {
    return (
      <div className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
        Loading entries...
      </div>
    );
  }

  const rows = entries.data ?? [];
  if (rows.length === 0) {
    return (
      <div className="border-t border-slate-100 px-5 py-3 text-xs text-slate-400">
        No entries on this date.
      </div>
    );
  }

  return (
    <div className="border-t border-slate-100 px-5 py-3">
      <div className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
        Entries
      </div>
      <table className="w-full text-sm">
        <thead className="text-xs uppercase tracking-wider text-slate-400">
          <tr>
            <th className="py-1 pr-2 text-left">Employee</th>
            <th className="py-1 pr-2 text-right">Pages</th>
            <th className="py-1 pr-2 text-right">Hours</th>
            <th className="py-1 pr-2 text-right">P/hr</th>
            {canWrite && <th className="py-1"></th>}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((e: ProductivityEntry) => {
            const hours = Number(e.hours_worked);
            const pph = hours > 0 ? e.pages / hours : 0;
            return (
              <tr key={e.id}>
                <td className="py-1.5 pr-2">{employeeName(e.employee_id)}</td>
                <td className="py-1.5 pr-2 text-right tabular-nums">
                  {e.pages}
                </td>
                <td className="py-1.5 pr-2 text-right tabular-nums">
                  {hours.toFixed(2)}
                </td>
                <td className="py-1.5 pr-2 text-right font-semibold tabular-nums">
                  {pph.toFixed(2)}
                </td>
                {canWrite && (
                  <td className="py-1.5 text-right">
                    <button
                      type="button"
                      className="btn-ghost text-rose-600 hover:bg-rose-50"
                      onClick={() => {
                        if (
                          window.confirm(
                            `Delete entry for ${employeeName(e.employee_id)} on ${e.entry_date}?`,
                          )
                        ) {
                          void remove.mutateAsync(e.id);
                        }
                      }}
                      aria-label="Delete entry"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
