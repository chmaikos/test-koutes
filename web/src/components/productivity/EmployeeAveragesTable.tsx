import clsx from "clsx";
import { AlertTriangle } from "lucide-react";
import type {
  EmployeeAverage,
  EmployeeAverages,
  EmployeePeriodAverage,
} from "@/api/types";
import { employeePerformanceStatus } from "@/components/productivity/reporting";

function AverageCell({ period }: { period: EmployeePeriodAverage }) {
  if (period.pages_per_day === null) {
    return <span className="text-slate-400">No data</span>;
  }
  return (
    <span
      className={clsx(
        "font-medium tabular-nums",
        period.below_minimum ? "text-rose-700" : "text-slate-800",
      )}
    >
      {period.pages_per_day.toFixed(2)}
    </span>
  );
}

function StatusBadge({ employee }: { employee: EmployeeAverage }) {
  const status = employeePerformanceStatus(employee);
  if (status === "excluded") {
    return (
      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
        Excluded
      </span>
    );
  }
  if (status === "consistently-below") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-xs font-medium text-rose-800">
        <AlertTriangle className="h-3 w-3" />
        Consistently below minimum
      </span>
    );
  }
  return <span className="text-xs text-slate-400">—</span>;
}

export function EmployeeAveragesTable({
  data,
  isLoading,
}: {
  data: EmployeeAverages | undefined;
  isLoading: boolean;
}) {
  return (
    <section className="border-t border-slate-100">
      <header className="px-5 py-3">
        <h3 className="font-semibold">Employee averages</h3>
        <p className="text-xs text-slate-500">
          Pages per 8-hour day.{" "}
          {data?.min_pages_per_day
            ? `Warehouse minimum: ${data.min_pages_per_day}.`
            : "No warehouse minimum is configured."}
        </p>
      </header>
      {isLoading ? (
        <p className="px-5 pb-4 text-sm text-slate-500">Loading averages...</p>
      ) : !data || data.employees.length === 0 ? (
        <p className="px-5 pb-4 text-sm text-slate-500">
          No active employees in this warehouse.
        </p>
      ) : (
        <>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-5 py-2.5 text-left">Employee</th>
                  <th className="px-4 py-2.5 text-right">Week</th>
                  <th className="px-4 py-2.5 text-right">Month</th>
                  <th className="px-4 py-2.5 text-right">3 months</th>
                  <th className="px-5 py-2.5 text-left">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.employees.map((employee) => (
                  <tr
                    key={employee.employee_id}
                    className={clsx(
                      employee.consistently_below_minimum && "bg-rose-50/60",
                    )}
                  >
                    <td className="px-5 py-3 font-medium">
                      {employee.employee_name}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <AverageCell period={employee.weekly} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <AverageCell period={employee.monthly} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <AverageCell period={employee.three_month} />
                    </td>
                    <td className="px-5 py-3">
                      <StatusBadge employee={employee} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="divide-y divide-slate-100 md:hidden">
            {data.employees.map((employee) => (
              <div
                key={employee.employee_id}
                className={clsx(
                  "space-y-2 px-5 py-3",
                  employee.consistently_below_minimum && "bg-rose-50/60",
                )}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{employee.employee_name}</span>
                  <StatusBadge employee={employee} />
                </div>
                <dl className="grid grid-cols-3 gap-2 text-xs">
                  <PeriodMetric label="Week" period={employee.weekly} />
                  <PeriodMetric label="Month" period={employee.monthly} />
                  <PeriodMetric label="3 months" period={employee.three_month} />
                </dl>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function PeriodMetric({
  label,
  period,
}: {
  label: string;
  period: EmployeePeriodAverage;
}) {
  return (
    <div>
      <dt className="text-slate-500">{label}</dt>
      <dd className="mt-0.5">
        <AverageCell period={period} />
      </dd>
    </div>
  );
}
