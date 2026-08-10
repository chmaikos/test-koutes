import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import {
  useRequestAnalytics,
  useRequestAssignees,
  useRequestReconciliation,
  useWarehouses,
} from "@/api/hooks";
import type {
  RequestDurationMetric,
  RequestIssueSeverity,
  RequestRateMetric,
  RequestReportFilters,
} from "@/api/types";
import { requestReportDateRange } from "@/pages/requestReporting";

const PAGE_SIZE = 50;
const SEVERITIES: RequestIssueSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
];
const ISSUE_TYPES = [
  "shortage",
  "excess",
  "typed_discrepancy",
  "open_follow_up",
  "missing_erp_document",
  "superseded_erp_document",
  "admin_override",
  "cancelled_reservation",
  "awaiting_confirmation",
  "operational_exception",
  "sla_breach",
  "review_pending_self_receipt",
];

function durationLabel(metric: RequestDurationMetric) {
  if (!metric.supported) return "Unsupported";
  if (metric.average_seconds === null) return "No samples";
  const hours = metric.average_seconds / 3600;
  return hours < 1
    ? `${Math.round(metric.average_seconds / 60)} min`
    : `${hours.toFixed(1)} hr`;
}

function rateLabel(metric: RequestRateMetric) {
  return metric.rate === null ? "—" : `${(metric.rate * 100).toFixed(1)}%`;
}

function severityClass(severity: RequestIssueSeverity) {
  return {
    critical: "bg-rose-100 text-rose-800",
    high: "bg-orange-100 text-orange-800",
    medium: "bg-amber-100 text-amber-800",
    low: "bg-slate-100 text-slate-700",
  }[severity];
}

export function RequestReconciliationPage() {
  const warehouses = useWarehouses(true);
  const [warehouseId, setWarehouseId] = useState<number | undefined>();
  const [assigneeId, setAssigneeId] = useState<number | undefined>();
  const [severity, setSeverity] = useState<RequestIssueSeverity | undefined>();
  const [issueType, setIssueType] = useState<string | undefined>();
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [page, setPage] = useState(1);
  const assignees = useRequestAssignees(warehouseId);
  const filters = useMemo<RequestReportFilters>(
    () => ({
      warehouse_id: warehouseId,
      assigned_mover_user_id: assigneeId,
      severity,
      issue_type: issueType,
      ...requestReportDateRange(fromDate, toDate),
    }),
    [warehouseId, assigneeId, severity, issueType, fromDate, toDate],
  );
  const reconciliation = useRequestReconciliation(filters, page, PAGE_SIZE);
  const analytics = useRequestAnalytics(filters);
  const totalPages = Math.max(
    1,
    Math.ceil((reconciliation.data?.total ?? 0) / PAGE_SIZE),
  );

  function resetPage() {
    setPage(1);
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Request reconciliation
          </h1>
          <p className="text-sm text-slate-500">
            Live workflow exceptions and request performance from source records.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            reconciliation.refetch();
            analytics.refetch();
          }}
        >
          <RefreshCw className="h-4 w-4" /> Refresh
        </button>
      </header>

      <section className="card card-pad grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <label>
          <span className="text-xs text-slate-500">Warehouse</span>
          <select
            className="input"
            value={warehouseId ?? ""}
            onChange={(event) => {
              setWarehouseId(
                event.target.value ? Number(event.target.value) : undefined,
              );
              setAssigneeId(undefined);
              resetPage();
            }}
          >
            <option value="">All accessible</option>
            {warehouses.data?.map((warehouse) => (
              <option key={warehouse.id} value={warehouse.id}>
                {warehouse.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="text-xs text-slate-500">Assignee</span>
          <select
            className="input"
            value={assigneeId ?? ""}
            disabled={!warehouseId}
            onChange={(event) => {
              setAssigneeId(
                event.target.value ? Number(event.target.value) : undefined,
              );
              resetPage();
            }}
          >
            <option value="">
              {warehouseId ? "All assignees" : "Choose warehouse first"}
            </option>
            {assignees.data?.map((assignee) => (
              <option key={assignee.id} value={assignee.id}>
                {assignee.display_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="text-xs text-slate-500">Severity</span>
          <select
            className="input"
            value={severity ?? ""}
            onChange={(event) => {
              setSeverity(
                (event.target.value as RequestIssueSeverity) || undefined,
              );
              resetPage();
            }}
          >
            <option value="">All</option>
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>
                {value[0].toUpperCase() + value.slice(1)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="text-xs text-slate-500">Issue type</span>
          <select
            className="input"
            value={issueType ?? ""}
            onChange={(event) => {
              setIssueType(event.target.value || undefined);
              resetPage();
            }}
          >
            <option value="">All</option>
            {ISSUE_TYPES.map((value) => (
              <option key={value} value={value}>
                {value.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="text-xs text-slate-500">From</span>
          <input
            className="input"
            type="date"
            value={fromDate}
            onChange={(event) => {
              setFromDate(event.target.value);
              resetPage();
            }}
          />
        </label>
        <label>
          <span className="text-xs text-slate-500">Through</span>
          <input
            className="input"
            type="date"
            value={toDate}
            onChange={(event) => {
              setToDate(event.target.value);
              resetPage();
            }}
          />
        </label>
      </section>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {(
          [
            ["Total", reconciliation.data?.summary.total ?? 0, "text-slate-900"],
            [
              "Critical",
              reconciliation.data?.summary.critical ?? 0,
              "text-rose-700",
            ],
            ["High", reconciliation.data?.summary.high ?? 0, "text-orange-700"],
            [
              "Medium",
              reconciliation.data?.summary.medium ?? 0,
              "text-amber-700",
            ],
            ["Low", reconciliation.data?.summary.low ?? 0, "text-slate-600"],
          ] as const
        ).map(([label, value, color]) => (
          <div key={label} className="card card-pad">
            <div className="text-xs uppercase tracking-wide text-slate-500">
              {label}
            </div>
            <div className={`mt-1 text-2xl font-semibold ${color}`}>{value}</div>
          </div>
        ))}
      </section>

      <section className="card overflow-hidden">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 className="font-semibold">Live issues</h2>
        </div>
        {reconciliation.isLoading ? (
          <p className="p-4 text-sm text-slate-500">Loading issues…</p>
        ) : reconciliation.data?.items.length ? (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Severity</th>
                    <th className="px-4 py-3">Issue</th>
                    <th className="px-4 py-3">Warehouse</th>
                    <th className="px-4 py-3">Assignee</th>
                    <th className="px-4 py-3">When</th>
                    <th className="px-4 py-3">Links</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {reconciliation.data.items.map((issue) => (
                    <tr key={issue.issue_key}>
                      <td className="px-4 py-3">
                        <span
                          className={`rounded-full px-2 py-1 text-xs font-medium ${severityClass(issue.severity)}`}
                        >
                          {issue.severity}
                        </span>
                      </td>
                      <td className="max-w-md px-4 py-3">
                        <div className="font-medium">{issue.title}</div>
                        <div className="text-xs text-slate-500">{issue.detail}</div>
                      </td>
                      <td className="px-4 py-3">{issue.warehouse_name}</td>
                      <td className="px-4 py-3">
                        {issue.assigned_mover_name ?? "Unassigned"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3">
                        {new Date(issue.occurred_at).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3">
                        <Link className="link" to={issue.request_path}>
                          Request #{issue.request_id}
                        </Link>
                        {issue.box_path && (
                          <>
                            {" · "}
                            <Link className="link" to={issue.box_path}>
                              Box #{issue.box_id}
                            </Link>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="divide-y divide-slate-100 md:hidden">
              {reconciliation.data.items.map((issue) => (
                <article key={issue.issue_key} className="space-y-2 p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div className="font-medium">{issue.title}</div>
                    <span
                      className={`rounded-full px-2 py-1 text-xs font-medium ${severityClass(issue.severity)}`}
                    >
                      {issue.severity}
                    </span>
                  </div>
                  <p className="text-sm text-slate-600">{issue.detail}</p>
                  <div className="text-xs text-slate-500">
                    {issue.warehouse_name} ·{" "}
                    {issue.assigned_mover_name ?? "Unassigned"} ·{" "}
                    {new Date(issue.occurred_at).toLocaleString()}
                  </div>
                  <Link className="link text-sm" to={issue.request_path}>
                    Open request #{issue.request_id}
                  </Link>
                  {issue.box_path && (
                    <Link className="link ml-3 text-sm" to={issue.box_path}>
                      Open box #{issue.box_id}
                    </Link>
                  )}
                </article>
              ))}
            </div>
          </>
        ) : (
          <p className="p-6 text-center text-sm text-slate-500">
            No reconciliation issues match these filters.
          </p>
        )}
        <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3">
          <span className="text-xs text-slate-500">
            Page {page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <button
              className="btn-secondary"
              disabled={page <= 1}
              onClick={() => setPage((value) => value - 1)}
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              className="btn-secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((value) => value + 1)}
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Request analytics</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {analytics.data &&
            [
              ["Approval", analytics.data.approval_duration],
              ["Preparation", analytics.data.preparation_duration],
              ["Transport start", analytics.data.transport_duration],
              ["Acceptance", analytics.data.acceptance_duration],
            ].map(([label, rawMetric]) => {
              const metric = rawMetric as RequestDurationMetric;
              return (
                <div key={label as string} className="card card-pad">
                  <div className="text-xs text-slate-500">{label as string}</div>
                  <div className="mt-1 text-xl font-semibold">
                    {durationLabel(metric)}
                  </div>
                  <div className="text-xs text-slate-400">
                    {metric.sample_size} sample(s)
                  </div>
                </div>
              );
            })}
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {analytics.data &&
            [
              ["On time", analytics.data.on_time],
              ["Discrepancy", analytics.data.discrepancy],
              ["Shortage", analytics.data.shortage],
              ["Overage", analytics.data.overage],
            ].map(([label, rawMetric]) => {
              const metric = rawMetric as RequestRateMetric;
              return (
                <div key={label as string} className="card card-pad">
                  <div className="text-xs text-slate-500">{label as string}</div>
                  <div className="mt-1 text-xl font-semibold">
                    {rateLabel(metric)}
                  </div>
                  <div className="text-xs text-slate-400">
                    {metric.numerator} / {metric.denominator}
                  </div>
                </div>
              );
            })}
        </div>
        {analytics.data && (
          <div className="grid gap-3 lg:grid-cols-2">
            <AnalyticsList
              title="Throughput by warehouse"
              items={analytics.data.throughput_by_warehouse.map((item) => ({
                label: item.name,
                value: `${item.completed_requests} requests · ${item.completed_quantity} boxes`,
              }))}
            />
            <AnalyticsList
              title="Throughput by mover"
              items={analytics.data.throughput_by_mover.map((item) => ({
                label: item.name,
                value: `${item.completed_requests} requests · ${item.completed_quantity} boxes`,
              }))}
            />
            <AnalyticsList
              title="Rejection reasons"
              items={analytics.data.rejection_reasons.map((item) => ({
                label: item.reason,
                value: String(item.count),
              }))}
            />
            <AnalyticsList
              title="Cancellation reasons"
              items={analytics.data.cancellation_reasons.map((item) => ({
                label: item.reason,
                value: String(item.count),
              }))}
            />
          </div>
        )}
      </section>
    </div>
  );
}

function AnalyticsList({
  title,
  items,
}: {
  title: string;
  items: { label: string; value: string }[];
}) {
  return (
    <div className="card card-pad">
      <h3 className="font-medium">{title}</h3>
      {items.length ? (
        <ul className="mt-3 divide-y divide-slate-100 text-sm">
          {items.map((item) => (
            <li key={`${item.label}:${item.value}`} className="flex gap-3 py-2">
              <span className="flex-1">{item.label}</span>
              <span className="text-slate-500">{item.value}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-slate-500">No data in this period.</p>
      )}
    </div>
  );
}
