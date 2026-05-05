import { Link } from "react-router-dom";
import { AlertTriangle, Boxes as BoxesIcon, TrendingDown, TrendingUp } from "lucide-react";
import { useDashboard } from "@/api/hooks";
import type { WarehouseSummary } from "@/api/types";

export function DashboardPage() {
  const { data, isLoading } = useDashboard();

  if (isLoading || !data) {
    return <p className="text-sm text-slate-500">Loading...</p>;
  }

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-slate-500">
            Live state across all 3 warehouses.
          </p>
        </div>
        <div className="flex gap-3 text-sm text-slate-500">
          <span className="inline-flex items-center gap-1">
            <BoxesIcon className="h-4 w-4" /> {data.total_active_boxes} active
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
  const pct = Math.min(
    100,
    Math.round((summary.inventory / Math.max(1, summary.max_capacity)) * 100),
  );
  const danger = summary.inventory >= summary.max_capacity;
  const low = summary.inventory < summary.min_inventory;

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
        <div className="text-right">
          <div className="text-2xl font-semibold">{summary.inventory}</div>
          <div className="text-xs text-slate-500">
            of {summary.max_capacity} capacity
          </div>
        </div>
      </div>

      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={
            danger
              ? "h-full bg-rose-500"
              : low
                ? "h-full bg-amber-400"
                : "h-full bg-brand-500"
          }
          style={{ width: `${pct}%` }}
        />
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-2 text-sm">
        <Stat
          label="Received today"
          value={summary.received_today}
          icon={<TrendingUp className="h-4 w-4 text-emerald-500" />}
        />
        <Stat
          label="Returned today"
          value={summary.returned_today}
          icon={<TrendingDown className="h-4 w-4 text-sky-500" />}
        />
        <Stat
          label="Received"
          value={summary.counts_by_status.received ?? 0}
        />
        <Stat
          label="Ready to return"
          value={summary.counts_by_status.ready_to_return ?? 0}
        />
      </dl>

      {summary.open_alerts > 0 && (
        <div className="mt-4 inline-flex items-center gap-2 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-700">
          <AlertTriangle className="h-3.5 w-3.5" /> {summary.open_alerts} open alert
          {summary.open_alerts > 1 ? "s" : ""}
        </div>
      )}
    </Link>
  );
}

function Stat({
  label,
  value,
  icon,
}: {
  label: string;
  value: number;
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
