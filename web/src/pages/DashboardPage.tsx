import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  Boxes as BoxesIcon,
  CheckCircle2,
  PackageCheck,
} from "lucide-react";
import { useDashboard } from "@/api/hooks";
import type { WarehouseSummary } from "@/api/types";

export function DashboardPage() {
  const { data, isLoading } = useDashboard();

  if (isLoading || !data) {
    return <p className="text-sm text-slate-500">Loading...</p>;
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-slate-500">
            Live state across all {data.warehouses.length} warehouses.
          </p>
        </div>
        <div className="flex flex-wrap gap-3 text-sm text-slate-500">
          <span className="inline-flex items-center gap-1">
            <BoxesIcon className="h-4 w-4 text-brand-600" />{" "}
            {data.total_available_boxes} available
          </span>
          <span className="inline-flex items-center gap-1">
            <PackageCheck className="h-4 w-4 text-emerald-600" />{" "}
            {data.total_unavailable_boxes} unavailable
          </span>
          <span className="inline-flex items-center gap-1">
            <CheckCircle2 className="h-4 w-4 text-indigo-500" />{" "}
            {data.total_completed_today} completed today
          </span>
          <span className="inline-flex items-center gap-1">
            <AlertTriangle className="h-4 w-4 text-amber-500" />{" "}
            {data.total_open_alerts} open alerts
          </span>
        </div>
      </header>

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {data.warehouses.map((w) => (
          <WarehouseCard key={w.warehouse_id} summary={w} />
        ))}
      </div>
    </div>
  );
}

function WarehouseCard({ summary }: { summary: WarehouseSummary }) {
  // Two independent bars: available compared to the minimum
  // (low = danger when we drop below) and unavailable compared to the
  // maximum (high = danger when we accumulate too much backlog).
  const lowAvailable = summary.available_boxes < summary.min_inventory;
  const highUnavailable = summary.inventory >= summary.max_capacity;
  // We want the "available" bar to read full when supply comfortably
  // exceeds the minimum, so divide by max(min, available). Once supply
  // drops below the minimum the bar visibly shrinks proportionally.
  const availableScale = Math.max(summary.min_inventory, summary.available_boxes, 1);
  const availablePct = Math.min(
    100,
    Math.round((summary.available_boxes / availableScale) * 100),
  );
  const unavailablePct = Math.min(
    100,
    Math.round(
      (summary.inventory / Math.max(1, summary.max_capacity)) * 100,
    ),
  );

  return (
    <Link
      to={`/boxes?warehouse_id=${summary.warehouse_id}`}
      className="card card-pad block transition hover:border-brand-300"
    >
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs uppercase tracking-wider text-slate-400">
            Warehouse
          </div>
          <div className="text-lg font-semibold">{summary.name}</div>
        </div>
        <div className="text-right text-xs text-slate-500">
          <div>
            min <span className="font-medium text-slate-700">{summary.min_inventory}</span>
          </div>
          <div>
            max <span className="font-medium text-slate-700">{summary.max_capacity}</span>
          </div>
        </div>
      </div>

      <BarMetric
        label="Available"
        hint="vs. min inventory"
        value={summary.available_boxes}
        anchor={summary.min_inventory}
        anchorLabel="min"
        pct={availablePct}
        tone={lowAvailable ? "danger" : "ok"}
      />
      <BarMetric
        label="Occupied"
        hint={`${summary.quarantined_boxes} quarantined · vs. max capacity`}
        value={summary.inventory}
        anchor={summary.max_capacity}
        anchorLabel="max"
        pct={unavailablePct}
        tone={highUnavailable ? "danger" : "warn"}
      />

      <dl className="mt-4 grid grid-cols-2 gap-2 text-sm">
        <Stat
          label="Completed today"
          value={summary.completed_today}
          icon={<CheckCircle2 className="h-4 w-4 text-indigo-500" />}
        />
        <Stat
          label="Boxes / day"
          value={summary.completed_per_day.toFixed(2)}
          icon={<Activity className="h-4 w-4 text-indigo-500" />}
        />
        <Stat
          label="Packaged for return"
          value={summary.ready_to_return_boxes}
          icon={<PackageCheck className="h-4 w-4 text-emerald-500" />}
        />
        <Stat
          label="Received today"
          value={summary.received_today}
        />
        {summary.quarantined_boxes > 0 && (
          <Stat label="Quarantined" value={summary.quarantined_boxes} />
        )}
      </dl>

      <ProductivitySection summary={summary} />

      {summary.open_alerts > 0 && (
        <div className="mt-4 inline-flex items-center gap-2 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-700">
          <AlertTriangle className="h-3.5 w-3.5" /> {summary.open_alerts} open alert
          {summary.open_alerts > 1 ? "s" : ""}
        </div>
      )}
    </Link>
  );
}

/**
 * Single metric line with a progress bar. Used by the dashboard for the
 * available-vs-min and unavailable-vs-max gauges so both bars share the
 * same DOM shape and only their colour/labels change.
 */
function BarMetric({
  label,
  hint,
  value,
  anchor,
  anchorLabel,
  pct,
  tone,
}: {
  label: string;
  hint: string;
  value: number;
  anchor: number;
  anchorLabel: string;
  pct: number;
  tone: "ok" | "warn" | "danger";
}) {
  const barClass =
    tone === "danger"
      ? "bg-rose-500"
      : tone === "warn"
        ? "bg-amber-400"
        : "bg-brand-500";
  return (
    <div className="mt-3">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <div className="font-medium text-slate-700">
          {label}{" "}
          <span className="text-xs font-normal text-slate-400">{hint}</span>
        </div>
        <div className="tabular-nums text-slate-600">
          <span className="text-base font-semibold text-slate-900">
            {value}
          </span>{" "}
          <span className="text-xs">
            / {anchor} {anchorLabel}
          </span>
        </div>
      </div>
      <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full ${barClass}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/**
 * Productivity slice of the warehouse card.
 *
 * Sits below the box stats (productivity is additive context, not a
 * replacement) and degrades to a "no entries today" hint rather than
 * disappearing -- absence is information for an operator who expected
 * a shift to have logged numbers by now.
 */
function ProductivitySection({ summary }: { summary: WarehouseSummary }) {
  const today = summary.productivity_today;
  const week = summary.productivity_week;
  if (!today && !week) {
    return null;
  }
  const todayPages = today?.total_pages ?? 0;
  const todayPpd = today?.avg_pages_per_day ?? 0;
  const weekPages = week?.total_pages ?? 0;
  const weekPpd = week?.avg_pages_per_day ?? 0;
  const topToday = today?.top?.[0];

  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="inline-flex items-center gap-1 font-medium text-slate-600">
          <Activity className="h-3.5 w-3.5 text-brand-600" /> Productivity
        </span>
        <Link
          to={`/productivity`}
          className="text-brand-700 hover:underline"
          onClick={(e) => e.stopPropagation()}
        >
          View
        </Link>
      </div>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <dt className="text-xs text-slate-500">Today</dt>
          <dd className="mt-0.5 font-medium tabular-nums">
            {todayPages} pages · {todayPpd.toFixed(2)} p/day
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">This week</dt>
          <dd className="mt-0.5 font-medium tabular-nums">
            {weekPages} pages · {weekPpd.toFixed(2)} p/day
          </dd>
        </div>
      </dl>
      {topToday ? (
        <div className="mt-2 text-xs text-slate-600">
          Top today: <span className="font-medium">{topToday.employee_name}</span>{" "}
          ({topToday.pages_per_day.toFixed(2)} p/day)
        </div>
      ) : (
        <div className="mt-2 text-xs text-slate-400">
          No entries logged today.
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  icon,
}: {
  label: string;
  // Accepts string for pre-formatted values (e.g. fixed-decimal rates).
  value: number | string;
  icon?: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 inline-flex items-center gap-1 font-medium">
        {icon}
        {value}
      </dd>
    </div>
  );
}
