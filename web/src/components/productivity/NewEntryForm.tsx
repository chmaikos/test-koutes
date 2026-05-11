import { useMemo, useState } from "react";
import { useUpsertProductivityEntry } from "@/api/hooks";
import type { Employee } from "@/api/types";

export function NewEntryForm({
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
  const [excluded, setExcluded] = useState(false);
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
                excluded_from_metrics: excluded,
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
      <label className="inline-flex items-center gap-2 text-xs text-slate-600 sm:col-span-4">
        <input
          type="checkbox"
          checked={excluded}
          onChange={(e) => setExcluded(e.target.checked)}
        />
        <span>
          Exclude this entry from metrics
          <span className="ml-1 text-slate-400">
            (training, equipment failure, partial shift…)
          </span>
        </span>
      </label>
      {error && <p className="text-xs text-rose-600 sm:col-span-4">{error}</p>}
    </div>
  );
}
