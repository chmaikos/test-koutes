import { useState } from "react";
import clsx from "clsx";
import { Link } from "react-router-dom";
import { Activity, ChevronRight } from "lucide-react";
import {
  useProductivityDailySummary,
  useProductivityWeeklySummary,
  useWarehouses,
} from "@/api/hooks";
import type {
  ProductivitySummary,
  Warehouse,
  WarehouseProductivity,
} from "@/api/types";
import { PerformersTable } from "@/components/productivity/PerformersTable";
import { isoWeekStart, todayStr } from "@/components/productivity/dates";

type Tab = "today" | "week";

export function ProductivityPage() {
  const { data: warehouses } = useWarehouses();
  const [tab, setTab] = useState<Tab>("today");
  const [date, setDate] = useState<string>(todayStr());

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
            Per-warehouse averages and leaderboards. Click a warehouse for the
            full entry list.
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
          label="Pages / day"
          value={summary.avg_pages_per_day.toFixed(2)}
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
}: {
  warehouse: Warehouse;
  summary: WarehouseProductivity | undefined;
  date: string;
  tab: Tab;
}) {
  // Preserve the index page's date/tab through to the detail view so an
  // operator drilling into a warehouse mid-week keeps seeing the same
  // period without re-selecting.
  const search = new URLSearchParams({ date, tab }).toString();
  return (
    <Link
      to={`/productivity/${warehouse.id}?${search}`}
      className="card block overflow-hidden transition hover:border-slate-300 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-400"
    >
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
          <ChevronRight
            className="h-4 w-4 text-slate-400"
            aria-hidden="true"
          />
        </div>
      </header>

      <div className="grid grid-cols-3 gap-3 px-5 py-4 text-sm">
        <Metric label="Pages" value={(summary?.total_pages ?? 0).toString()} />
        <Metric label="Hours" value={(summary?.total_hours ?? 0).toFixed(2)} />
        <Metric
          label="Pages / day"
          value={(summary?.avg_pages_per_day ?? 0).toFixed(2)}
        />
      </div>

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
    </Link>
  );
}
