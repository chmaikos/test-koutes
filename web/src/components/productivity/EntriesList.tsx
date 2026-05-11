import clsx from "clsx";
import { Trash2 } from "lucide-react";
import {
  useDeleteProductivityEntry,
  useEmployees,
  useProductivityEntries,
} from "@/api/hooks";
import type { ProductivityEntry } from "@/api/types";

export function EntriesList({
  warehouseId,
  fromDate,
  toDate,
  canWrite,
  title = "Entries",
}: {
  warehouseId: number;
  fromDate: string;
  toDate: string;
  canWrite: boolean;
  title?: string;
}) {
  const entries = useProductivityEntries({
    warehouse_id: warehouseId,
    from_date: fromDate,
    to_date: toDate,
  });
  const remove = useDeleteProductivityEntry();
  const employees = useEmployees(warehouseId, true);
  const employeeById = (id: number) =>
    employees.data?.items.find((e) => e.id === id);
  const employeeName = (id: number) =>
    employeeById(id)?.full_name ?? `#${id}`;

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
        No entries in this period.
      </div>
    );
  }

  return (
    <div className="border-t border-slate-100 px-5 py-3">
      <div className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
        {title}
      </div>
      <table className="w-full text-sm">
        <thead className="text-xs uppercase tracking-wider text-slate-400">
          <tr>
            <th className="py-1 pr-2 text-left">Date</th>
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
            const employee = employeeById(e.employee_id);
            // Two distinct reasons a row can be "excluded": the entry
            // itself was flagged at create time, or the admin has set
            // the whole employee aside. Both should render the same
            // way -- dimmed strikethrough numbers + a tag explaining
            // why -- so the operator immediately sees the row doesn't
            // count toward the totals above.
            const entryExcluded = e.excluded_from_metrics;
            const employeeExcluded =
              !!employee?.excluded_from_metrics;
            const excluded = entryExcluded || employeeExcluded;
            const excludedReason = entryExcluded
              ? "Entry excluded"
              : employeeExcluded
                ? "Employee excluded"
                : null;
            return (
              <tr
                key={e.id}
                className={clsx(excluded && "text-slate-400")}
                title={excluded ? "Not counted in metrics" : undefined}
              >
                <td className="py-1.5 pr-2 tabular-nums">
                  <span
                    className={clsx(
                      excluded ? "text-slate-400" : "text-slate-500",
                    )}
                  >
                    {e.entry_date}
                  </span>
                </td>
                <td className="py-1.5 pr-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span>{employeeName(e.employee_id)}</span>
                    {excludedReason && (
                      <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-amber-800">
                        {excludedReason}
                      </span>
                    )}
                  </div>
                </td>
                <td
                  className={clsx(
                    "py-1.5 pr-2 text-right tabular-nums",
                    excluded && "line-through",
                  )}
                >
                  {e.pages}
                </td>
                <td
                  className={clsx(
                    "py-1.5 pr-2 text-right tabular-nums",
                    excluded && "line-through",
                  )}
                >
                  {hours.toFixed(2)}
                </td>
                <td
                  className={clsx(
                    "py-1.5 pr-2 text-right font-semibold tabular-nums",
                    excluded && "line-through",
                  )}
                >
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
