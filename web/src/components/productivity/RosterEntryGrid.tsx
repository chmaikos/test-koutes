import {
  type ChangeEvent,
  type Dispatch,
  type FocusEvent,
  type KeyboardEvent,
  type SetStateAction,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import clsx from "clsx";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  useDeleteProductivityEntry,
  useEmployees,
  useProductivityEntries,
  useUpsertProductivityEntry,
} from "@/api/hooks";
import type { Employee, ProductivityEntry } from "@/api/types";

type RowStatus = "idle" | "dirty" | "saving" | "saved" | "error";

type FilterMode = "all" | "missing" | "entered";

interface RowState {
  pages: string;
  hours: string;
  note: string;
  excluded: boolean;
  status: RowStatus;
  errorMsg?: string;
  expanded: boolean;
  pendingDelete: boolean;
}

type RowMap = Record<number, RowState>;

const PAGES_COL = "pages" as const;
const HOURS_COL = "hours" as const;
type ColKey = typeof PAGES_COL | typeof HOURS_COL;

function defaultHoursText(emp: Employee): string {
  const parsed = Number(emp.default_hours_per_day);
  if (!Number.isFinite(parsed) || parsed <= 0) return "";
  // Trim trailing zeros: 8.00 -> "8", 7.50 -> "7.5"
  return String(parsed);
}

function buildRowFromEntry(
  _emp: Employee,
  entry: ProductivityEntry | undefined,
): RowState {
  if (!entry) {
    return {
      pages: "",
      hours: "",
      note: "",
      excluded: false,
      status: "idle",
      expanded: false,
      pendingDelete: false,
    };
  }
  const hoursNumber = Number(entry.hours_worked);
  return {
    pages: String(entry.pages),
    hours: Number.isFinite(hoursNumber) ? String(hoursNumber) : "",
    note: entry.note ?? "",
    excluded: entry.excluded_from_metrics,
    status: "saved",
    expanded: false,
    pendingDelete: false,
  };
}

function parsePages(value: string): number | null {
  const v = value.trim();
  if (v === "") return null;
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) return null;
  return n;
}

function parseHours(value: string): number | null {
  const v = value.trim();
  if (v === "") return null;
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0 || n > 24) return null;
  return n;
}

function formatDateCaption(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function RosterEntryGrid({
  warehouseId,
  date,
  canWrite,
}: {
  warehouseId: number;
  date: string;
  canWrite: boolean;
}) {
  const employeesQuery = useEmployees(warehouseId, false, { pageSize: 500 });
  const entriesQuery = useProductivityEntries({
    warehouse_id: warehouseId,
    from_date: date,
    to_date: date,
  });
  const upsert = useUpsertProductivityEntry();
  const remove = useDeleteProductivityEntry();

  const employees = useMemo(
    () =>
      (employeesQuery.data?.items ?? [])
        .slice()
        // Group employees by shift length first (longest first so full-time
        // shifts sit at the top) and alphabetize within each group. This
        // matches how operators tend to think about the roster -- "the
        // 8-hour folks" vs "the part-timers".
        .sort((a, b) => {
          const ah = Number(a.default_hours_per_day);
          const bh = Number(b.default_hours_per_day);
          const aHours = Number.isFinite(ah) ? ah : 0;
          const bHours = Number.isFinite(bh) ? bh : 0;
          if (aHours !== bHours) return bHours - aHours;
          return a.full_name.localeCompare(b.full_name, undefined, {
            sensitivity: "base",
          });
        }),
    [employeesQuery.data],
  );

  const entriesByEmployee = useMemo(() => {
    const map = new Map<number, ProductivityEntry>();
    for (const e of entriesQuery.data ?? []) {
      map.set(e.employee_id, e);
    }
    return map;
  }, [entriesQuery.data]);

  // Local per-row state. Seeded from server data, but operator edits are
  // authoritative until they blur or hit Enter -- otherwise React Query
  // refetches would clobber a half-typed value.
  const [rows, setRows] = useState<RowMap>({});

  // Track which rows the operator has actively touched. Untouched rows are
  // safe to overwrite when the server snapshot changes (e.g. switching the
  // selected date); touched rows we preserve until they're saved.
  const touchedRef = useRef<Set<number>>(new Set());

  useEffect(() => {
    setRows((prev) => {
      const next: RowMap = {};
      for (const emp of employees) {
        const existing = prev[emp.id];
        const serverEntry = entriesByEmployee.get(emp.id);
        const touched = touchedRef.current.has(emp.id);
        if (existing && touched && existing.status !== "saved") {
          next[emp.id] = existing;
          continue;
        }
        next[emp.id] = buildRowFromEntry(emp, serverEntry);
      }
      return next;
    });
  }, [employees, entriesByEmployee]);

  // Reset touched tracking when the date changes -- yesterday's dirty edits
  // shouldn't bleed into today's roster.
  useEffect(() => {
    touchedRef.current = new Set();
  }, [date]);

  const [filter, setFilter] = useState<FilterMode>("all");

  const inputRefs = useRef<Map<string, HTMLInputElement | null>>(new Map());
  const registerInput = useCallback(
    (employeeId: number, col: ColKey) =>
      (el: HTMLInputElement | null) => {
        const key = `${employeeId}:${col}`;
        if (el) inputRefs.current.set(key, el);
        else inputRefs.current.delete(key);
      },
    [],
  );

  const focusCell = useCallback((employeeId: number, col: ColKey) => {
    const el = inputRefs.current.get(`${employeeId}:${col}`);
    if (el) {
      el.focus();
      el.select();
    }
  }, []);

  const visibleEmployees = useMemo(() => {
    if (filter === "all") return employees;
    return employees.filter((emp) => {
      const hasEntry = entriesByEmployee.has(emp.id);
      return filter === "entered" ? hasEntry : !hasEntry;
    });
  }, [employees, entriesByEmployee, filter]);

  const missingCount = useMemo(
    () =>
      employees.reduce(
        (acc, emp) => acc + (entriesByEmployee.has(emp.id) ? 0 : 1),
        0,
      ),
    [employees, entriesByEmployee],
  );

  const updateRow = useCallback(
    (employeeId: number, patch: Partial<RowState>) => {
      setRows((prev) => {
        const current = prev[employeeId];
        if (!current) return prev;
        return { ...prev, [employeeId]: { ...current, ...patch } };
      });
    },
    [],
  );

  const markTouched = useCallback((employeeId: number) => {
    touchedRef.current.add(employeeId);
  }, []);

  const revertRow = useCallback(
    (employeeId: number) => {
      const emp = employees.find((e) => e.id === employeeId);
      if (!emp) return;
      const serverEntry = entriesByEmployee.get(employeeId);
      touchedRef.current.delete(employeeId);
      updateRow(employeeId, buildRowFromEntry(emp, serverEntry));
    },
    [employees, entriesByEmployee, updateRow],
  );

  const saveRow = useCallback(
    async (employeeId: number, row: RowState): Promise<boolean> => {
      const pages = parsePages(row.pages);
      const hours = parseHours(row.hours);
      if (pages === null || hours === null) {
        return false;
      }
      updateRow(employeeId, { status: "saving", errorMsg: undefined });
      try {
        await upsert.mutateAsync({
          employee_id: employeeId,
          entry_date: date,
          pages,
          hours_worked: hours,
          note: row.note.trim() || undefined,
          excluded_from_metrics: row.excluded,
        });
        touchedRef.current.delete(employeeId);
        updateRow(employeeId, {
          status: "saved",
          errorMsg: undefined,
          pages: String(pages),
          hours: String(hours),
        });
        return true;
      } catch (err: unknown) {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response
            ?.data?.detail ?? "Failed to save";
        updateRow(employeeId, {
          status: "error",
          errorMsg:
            typeof detail === "string" ? detail : "Failed to save",
        });
        return false;
      }
    },
    [date, updateRow, upsert],
  );

  const handleCellBlur = useCallback(
    (employeeId: number) => {
      const row = rows[employeeId];
      if (!row) return;
      if (row.status !== "dirty") return;
      const pages = parsePages(row.pages);
      const hours = parseHours(row.hours);
      if (pages === null || hours === null) {
        // Wait until the other cell is filled in; don't fire an invalid save.
        return;
      }
      void saveRow(employeeId, row);
    },
    [rows, saveRow],
  );

  const handleEnter = useCallback(
    async (employeeId: number, col: ColKey) => {
      const row = rows[employeeId];
      if (!row) return;
      let ok = true;
      if (row.status === "dirty") {
        ok = await saveRow(employeeId, row);
      }
      if (!ok) return;
      const index = visibleEmployees.findIndex((e) => e.id === employeeId);
      if (index < 0) return;
      const next = visibleEmployees[index + 1];
      if (next) focusCell(next.id, col);
    },
    [focusCell, rows, saveRow, visibleEmployees],
  );

  const handlePrefillStandardHours = useCallback(() => {
    let firstMissing: number | null = null;
    setRows((prev) => {
      const next: RowMap = { ...prev };
      for (const emp of employees) {
        if (entriesByEmployee.has(emp.id)) continue;
        const current = next[emp.id];
        if (!current) continue;
        if (current.hours.trim() !== "") continue;
        const fill = defaultHoursText(emp);
        if (!fill) continue;
        if (firstMissing === null) firstMissing = emp.id;
        touchedRef.current.add(emp.id);
        next[emp.id] = {
          ...current,
          hours: fill,
          status: "dirty",
        };
      }
      return next;
    });
    if (firstMissing !== null) {
      // Defer to next tick so the inputs reflect the new value before focusing.
      const target = firstMissing;
      window.setTimeout(() => focusCell(target, PAGES_COL), 0);
    }
  }, [employees, entriesByEmployee, focusCell]);

  const handleDelete = useCallback(
    async (employeeId: number) => {
      const entry = entriesByEmployee.get(employeeId);
      if (!entry) return;
      try {
        await remove.mutateAsync(entry.id);
        const emp = employees.find((e) => e.id === employeeId);
        if (emp) {
          touchedRef.current.delete(employeeId);
          updateRow(employeeId, buildRowFromEntry(emp, undefined));
        }
      } catch (err: unknown) {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response
            ?.data?.detail ?? "Failed to delete";
        updateRow(employeeId, {
          status: "error",
          errorMsg:
            typeof detail === "string" ? detail : "Failed to delete",
        });
      }
    },
    [employees, entriesByEmployee, remove, updateRow],
  );

  const isLoading =
    employeesQuery.isLoading ||
    (entriesQuery.isLoading && !entriesQuery.data);

  if (isLoading) {
    return (
      <div className="border-t border-slate-100 px-5 py-6 text-sm text-slate-500">
        Loading roster...
      </div>
    );
  }

  if (employees.length === 0) {
    return (
      <div className="border-t border-slate-100 bg-slate-50/60 px-5 py-6 text-sm text-slate-500">
        No active employees in this warehouse. Add one in{" "}
        <a className="underline" href="/settings">
          Settings
        </a>
        .
      </div>
    );
  }

  return (
    <div className="border-t border-slate-100">
      <div className="flex flex-col gap-3 px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-slate-500">
            Roster
          </div>
          <p className="text-xs text-slate-500">
            Editing entries for{" "}
            <span className="font-medium text-slate-700">
              {formatDateCaption(date)}
            </span>
            {missingCount > 0 && canWrite && (
              <>
                {" "}
                · {missingCount} still missing
              </>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <FilterChips
            value={filter}
            onChange={setFilter}
            counts={{
              all: employees.length,
              missing: missingCount,
              entered: employees.length - missingCount,
            }}
          />
          {canWrite && missingCount > 0 && (
            <button
              type="button"
              className="btn-secondary"
              onClick={handlePrefillStandardHours}
              title="Pre-fill standard hours for everyone still missing an entry"
            >
              <Sparkles className="h-4 w-4" />
              Pre-fill hours
            </button>
          )}
        </div>
      </div>

      {visibleEmployees.length === 0 ? (
        <div className="px-5 py-6 text-center text-xs text-slate-400">
          No employees match this filter.
        </div>
      ) : (
        <>
          <div className="hidden sm:block">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-slate-400">
                <tr className="border-b border-slate-100">
                  <th className="w-28 px-5 py-2 text-left">Status</th>
                  <th className="px-2 py-2 text-left">Employee</th>
                  <th className="w-28 px-2 py-2 text-right">Pages</th>
                  <th className="w-28 px-2 py-2 text-right">Hours</th>
                  <th className="w-20 px-2 py-2 text-right">P/hr</th>
                  <th className="w-10 px-2 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {visibleEmployees.map((emp) => {
                  const row = rows[emp.id];
                  if (!row) return null;
                  const entry = entriesByEmployee.get(emp.id);
                  return (
                    <RosterRow
                      key={emp.id}
                      employee={emp}
                      row={row}
                      entry={entry}
                      canWrite={canWrite}
                      onChange={(patch) => {
                        markTouched(emp.id);
                        updateRow(emp.id, {
                          ...patch,
                          status: "dirty",
                          errorMsg: undefined,
                        });
                      }}
                      onBlur={() => handleCellBlur(emp.id)}
                      onEnter={(col) => handleEnter(emp.id, col)}
                      onEscape={() => revertRow(emp.id)}
                      onToggleExpand={() =>
                        updateRow(emp.id, { expanded: !row.expanded })
                      }
                      onDetailsCommit={() => handleCellBlur(emp.id)}
                      onSetExcluded={(value) => {
                        markTouched(emp.id);
                        updateRow(emp.id, {
                          excluded: value,
                          status: "dirty",
                          errorMsg: undefined,
                        });
                      }}
                      onSetNote={(value) => {
                        markTouched(emp.id);
                        updateRow(emp.id, {
                          note: value,
                          status: "dirty",
                          errorMsg: undefined,
                        });
                      }}
                      onRequestDelete={() =>
                        updateRow(emp.id, { pendingDelete: true })
                      }
                      onCancelDelete={() =>
                        updateRow(emp.id, { pendingDelete: false })
                      }
                      onConfirmDelete={() => {
                        updateRow(emp.id, { pendingDelete: false });
                        void handleDelete(emp.id);
                      }}
                      registerInput={registerInput}
                      isDeleting={remove.isPending}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="space-y-3 px-5 py-3 sm:hidden">
            {visibleEmployees.map((emp) => {
              const row = rows[emp.id];
              if (!row) return null;
              const entry = entriesByEmployee.get(emp.id);
              return (
                <RosterCard
                  key={emp.id}
                  employee={emp}
                  row={row}
                  entry={entry}
                  canWrite={canWrite}
                  onChange={(patch) => {
                    markTouched(emp.id);
                    updateRow(emp.id, {
                      ...patch,
                      status: "dirty",
                      errorMsg: undefined,
                    });
                  }}
                  onBlur={() => handleCellBlur(emp.id)}
                  onEnter={(col) => handleEnter(emp.id, col)}
                  onEscape={() => revertRow(emp.id)}
                  onToggleExpand={() =>
                    updateRow(emp.id, { expanded: !row.expanded })
                  }
                  onSetExcluded={(value) => {
                    markTouched(emp.id);
                    updateRow(emp.id, {
                      excluded: value,
                      status: "dirty",
                      errorMsg: undefined,
                    });
                  }}
                  onSetNote={(value) => {
                    markTouched(emp.id);
                    updateRow(emp.id, {
                      note: value,
                      status: "dirty",
                      errorMsg: undefined,
                    });
                  }}
                  onDetailsCommit={() => handleCellBlur(emp.id)}
                  onRequestDelete={() =>
                    updateRow(emp.id, { pendingDelete: true })
                  }
                  onCancelDelete={() =>
                    updateRow(emp.id, { pendingDelete: false })
                  }
                  onConfirmDelete={() => {
                    updateRow(emp.id, { pendingDelete: false });
                    void handleDelete(emp.id);
                  }}
                  registerInput={registerInput}
                  isDeleting={remove.isPending}
                />
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

interface RowCallbacks {
  canWrite: boolean;
  onChange: (patch: Partial<RowState>) => void;
  onBlur: () => void;
  onEnter: (col: ColKey) => void;
  onEscape: () => void;
  onToggleExpand: () => void;
  onSetExcluded: (value: boolean) => void;
  onSetNote: (value: string) => void;
  onDetailsCommit: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
  registerInput: (
    employeeId: number,
    col: ColKey,
  ) => (el: HTMLInputElement | null) => void;
  isDeleting: boolean;
}

function RosterRow({
  employee,
  row,
  entry,
  canWrite,
  onChange,
  onBlur,
  onEnter,
  onEscape,
  onToggleExpand,
  onSetExcluded,
  onSetNote,
  onDetailsCommit,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
  registerInput,
  isDeleting,
}: {
  employee: Employee;
  row: RowState;
  entry: ProductivityEntry | undefined;
} & RowCallbacks) {
  const pagesNum = Number(row.pages);
  const hoursNum = Number(row.hours);
  const pph =
    Number.isFinite(pagesNum) && Number.isFinite(hoursNum) && hoursNum > 0
      ? pagesNum / hoursNum
      : null;
  const employeeExcluded = employee.excluded_from_metrics;
  const showExcludedStyle = row.excluded || employeeExcluded;
  const defaultLabel = defaultHoursText(employee);

  return (
    <>
      <tr
        className={clsx(
          "align-middle",
          showExcludedStyle && "text-slate-400",
        )}
      >
        <td className="px-5 py-2">
          <StatusPill
            row={row}
            employeeExcluded={employeeExcluded}
            hasEntry={!!entry}
          />
        </td>
        <td className="px-2 py-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="flex items-center gap-1 text-left text-slate-800 hover:text-slate-900"
              onClick={onToggleExpand}
              aria-expanded={row.expanded}
              aria-label={row.expanded ? "Hide details" : "Show details"}
            >
              {row.expanded ? (
                <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 text-slate-400" />
              )}
              <span className="font-medium">{employee.full_name}</span>
            </button>
            {defaultLabel && (
              <span className="text-xs text-slate-400">
                Default {defaultLabel}h
              </span>
            )}
          </div>
        </td>
        <td className="px-2 py-2 text-right">
          <InlineNumber
            value={row.pages}
            placeholder="0"
            ariaLabel={`Pages for ${employee.full_name}`}
            inputMode="numeric"
            min={0}
            step={1}
            disabled={!canWrite}
            inputRef={registerInput(employee.id, PAGES_COL)}
            onChange={(value) => onChange({ pages: value })}
            onBlur={onBlur}
            onEnter={() => onEnter(PAGES_COL)}
            onEscape={onEscape}
          />
        </td>
        <td className="px-2 py-2 text-right">
          <InlineNumber
            value={row.hours}
            placeholder={defaultLabel || "0"}
            ariaLabel={`Hours for ${employee.full_name}`}
            inputMode="decimal"
            min={0.25}
            step={0.25}
            max={24}
            disabled={!canWrite}
            inputRef={registerInput(employee.id, HOURS_COL)}
            onChange={(value) => onChange({ hours: value })}
            onBlur={onBlur}
            onEnter={() => onEnter(HOURS_COL)}
            onEscape={onEscape}
          />
        </td>
        <td
          className={clsx(
            "px-2 py-2 text-right font-semibold tabular-nums",
            showExcludedStyle && "line-through",
            pph === null && "text-slate-300",
          )}
        >
          {pph === null ? "—" : pph.toFixed(2)}
        </td>
        <td className="px-2 py-2 text-right">
          {canWrite && entry && (
            <button
              type="button"
              className="btn-ghost text-slate-500 hover:bg-slate-100"
              onClick={onToggleExpand}
              aria-label="More options"
            >
              <ChevronDown
                className={clsx(
                  "h-4 w-4 transition",
                  row.expanded && "rotate-180",
                )}
              />
            </button>
          )}
        </td>
      </tr>
      {row.expanded && canWrite && (
        <tr className="bg-slate-50/60">
          <td colSpan={6} className="px-5 py-3">
            <RowDetails
              row={row}
              hasEntry={!!entry}
              isDeleting={isDeleting}
              onSetExcluded={onSetExcluded}
              onSetNote={onSetNote}
              onCommit={onDetailsCommit}
              onRequestDelete={onRequestDelete}
              onCancelDelete={onCancelDelete}
              onConfirmDelete={onConfirmDelete}
            />
          </td>
        </tr>
      )}
      {row.status === "error" && row.errorMsg && (
        <tr>
          <td colSpan={6} className="px-5 pb-2 text-xs text-rose-600">
            {row.errorMsg}
          </td>
        </tr>
      )}
    </>
  );
}

function RosterCard({
  employee,
  row,
  entry,
  canWrite,
  onChange,
  onBlur,
  onEnter,
  onEscape,
  onToggleExpand,
  onSetExcluded,
  onSetNote,
  onDetailsCommit,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
  registerInput,
  isDeleting,
}: {
  employee: Employee;
  row: RowState;
  entry: ProductivityEntry | undefined;
} & RowCallbacks) {
  const pagesNum = Number(row.pages);
  const hoursNum = Number(row.hours);
  const pph =
    Number.isFinite(pagesNum) && Number.isFinite(hoursNum) && hoursNum > 0
      ? pagesNum / hoursNum
      : null;
  const employeeExcluded = employee.excluded_from_metrics;
  const showExcludedStyle = row.excluded || employeeExcluded;
  const defaultLabel = defaultHoursText(employee);

  return (
    <div
      className={clsx(
        "rounded-lg border border-slate-200 bg-white p-3",
        showExcludedStyle && "text-slate-400",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium text-slate-800">
            {employee.full_name}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
            <StatusPill
              row={row}
              employeeExcluded={employeeExcluded}
              hasEntry={!!entry}
            />
            {defaultLabel && <span>Default {defaultLabel}h</span>}
          </div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <label className="block text-xs text-slate-500">
          Pages
          <InlineNumber
            value={row.pages}
            placeholder="0"
            ariaLabel={`Pages for ${employee.full_name}`}
            inputMode="numeric"
            min={0}
            step={1}
            disabled={!canWrite}
            inputRef={registerInput(employee.id, PAGES_COL)}
            onChange={(value) => onChange({ pages: value })}
            onBlur={onBlur}
            onEnter={() => onEnter(PAGES_COL)}
            onEscape={onEscape}
            fullWidth
          />
        </label>
        <label className="block text-xs text-slate-500">
          Hours
          <InlineNumber
            value={row.hours}
            placeholder={defaultLabel || "0"}
            ariaLabel={`Hours for ${employee.full_name}`}
            inputMode="decimal"
            min={0.25}
            step={0.25}
            max={24}
            disabled={!canWrite}
            inputRef={registerInput(employee.id, HOURS_COL)}
            onChange={(value) => onChange({ hours: value })}
            onBlur={onBlur}
            onEnter={() => onEnter(HOURS_COL)}
            onEscape={onEscape}
            fullWidth
          />
        </label>
      </div>
      <div className="mt-2 flex items-center justify-between text-xs">
        <span
          className={clsx(
            "tabular-nums",
            showExcludedStyle && "line-through",
            pph === null ? "text-slate-300" : "font-semibold text-slate-700",
          )}
        >
          {pph === null ? "—" : `${pph.toFixed(2)} P/hr`}
        </span>
        {canWrite && (
          <button
            type="button"
            className="inline-flex items-center gap-1 text-slate-500 hover:text-slate-700"
            onClick={onToggleExpand}
            aria-expanded={row.expanded}
          >
            {row.expanded ? "Hide details" : "Details"}
            <ChevronDown
              className={clsx(
                "h-3.5 w-3.5 transition",
                row.expanded && "rotate-180",
              )}
            />
          </button>
        )}
      </div>
      {row.expanded && canWrite && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <RowDetails
            row={row}
            hasEntry={!!entry}
            isDeleting={isDeleting}
            onSetExcluded={onSetExcluded}
            onSetNote={onSetNote}
            onCommit={onDetailsCommit}
            onRequestDelete={onRequestDelete}
            onCancelDelete={onCancelDelete}
            onConfirmDelete={onConfirmDelete}
          />
        </div>
      )}
      {row.status === "error" && row.errorMsg && (
        <p className="mt-2 text-xs text-rose-600">{row.errorMsg}</p>
      )}
    </div>
  );
}

function RowDetails({
  row,
  hasEntry,
  isDeleting,
  onSetExcluded,
  onSetNote,
  onCommit,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  row: RowState;
  hasEntry: boolean;
  isDeleting: boolean;
  onSetExcluded: (value: boolean) => void;
  onSetNote: (value: string) => void;
  onCommit: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
      <label className="block text-xs text-slate-500">
        <span>Note (optional)</span>
        <input
          className="input"
          value={row.note}
          maxLength={500}
          onChange={(e) => onSetNote(e.target.value)}
          onBlur={onCommit}
          placeholder="Training, equipment failure, partial shift…"
        />
      </label>
      <div className="flex items-center justify-between gap-3 sm:flex-col sm:items-end">
        <label className="inline-flex items-center gap-2 text-xs text-slate-600">
          <input
            type="checkbox"
            checked={row.excluded}
            onChange={(e) => {
              onSetExcluded(e.target.checked);
              // Commit immediately so the totals update without an extra blur.
              window.setTimeout(onCommit, 0);
            }}
          />
          <span>Exclude from metrics</span>
        </label>
        {hasEntry &&
          (row.pendingDelete ? (
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-500">Delete entry?</span>
              <button
                type="button"
                className="btn-secondary"
                onClick={onCancelDelete}
                disabled={isDeleting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-danger"
                onClick={onConfirmDelete}
                disabled={isDeleting}
              >
                Confirm
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn-ghost text-rose-600 hover:bg-rose-50"
              onClick={onRequestDelete}
            >
              <Trash2 className="h-4 w-4" />
              Delete entry
            </button>
          ))}
      </div>
    </div>
  );
}

function StatusPill({
  row,
  employeeExcluded,
  hasEntry,
}: {
  row: RowState;
  employeeExcluded: boolean;
  hasEntry: boolean;
}) {
  if (row.status === "saving") {
    return (
      <span className="badge bg-slate-100 text-slate-600">
        <Loader2 className="h-3 w-3 animate-spin" />
        Saving
      </span>
    );
  }
  if (row.status === "error") {
    return (
      <span className="badge bg-rose-100 text-rose-700">
        <AlertTriangle className="h-3 w-3" />
        Error
      </span>
    );
  }
  if (row.status === "dirty") {
    return (
      <span className="badge bg-amber-100 text-amber-800">Unsaved</span>
    );
  }
  if (employeeExcluded || row.excluded) {
    return (
      <span className="badge bg-amber-100 text-amber-800">Excluded</span>
    );
  }
  if (hasEntry || row.status === "saved") {
    return (
      <span className="badge bg-emerald-100 text-emerald-700">
        <Check className="h-3 w-3" />
        Saved
      </span>
    );
  }
  return (
    <span className="badge bg-slate-100 text-slate-500">Not entered</span>
  );
}

function FilterChips({
  value,
  onChange,
  counts,
}: {
  value: FilterMode;
  onChange: Dispatch<SetStateAction<FilterMode>>;
  counts: { all: number; missing: number; entered: number };
}) {
  const options: { id: FilterMode; label: string; count: number }[] = [
    { id: "all", label: "All", count: counts.all },
    { id: "missing", label: "Missing", count: counts.missing },
    { id: "entered", label: "Entered", count: counts.entered },
  ];
  return (
    <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-1 text-xs font-medium">
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onChange(opt.id)}
          className={clsx(
            "rounded-md px-2.5 py-1 transition",
            value === opt.id
              ? "bg-white text-slate-900 shadow-sm"
              : "text-slate-600 hover:text-slate-900",
          )}
        >
          {opt.label}
          <span
            className={clsx(
              "ml-1 tabular-nums",
              value === opt.id ? "text-slate-500" : "text-slate-400",
            )}
          >
            {opt.count}
          </span>
        </button>
      ))}
    </div>
  );
}

function InlineNumber({
  value,
  placeholder,
  ariaLabel,
  inputMode,
  min,
  max,
  step,
  disabled,
  inputRef,
  onChange,
  onBlur,
  onEnter,
  onEscape,
  fullWidth,
}: {
  value: string;
  placeholder?: string;
  ariaLabel: string;
  inputMode: "numeric" | "decimal";
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  inputRef?: (el: HTMLInputElement | null) => void;
  onChange: (value: string) => void;
  onBlur: () => void;
  onEnter: () => void;
  onEscape: () => void;
  fullWidth?: boolean;
}) {
  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    onChange(e.target.value);
  };
  const handleFocus = (e: FocusEvent<HTMLInputElement>) => {
    e.currentTarget.select();
  };
  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      e.currentTarget.blur();
      onEnter();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      onEscape();
      e.currentTarget.blur();
    }
  };
  return (
    <input
      ref={inputRef}
      type="number"
      className={clsx(
        "input text-right tabular-nums",
        !fullWidth && "max-w-[7rem]",
      )}
      inputMode={inputMode}
      min={min}
      max={max}
      step={step}
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={handleChange}
      onFocus={handleFocus}
      onBlur={onBlur}
      onKeyDown={handleKeyDown}
    />
  );
}
