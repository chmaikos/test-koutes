import { useMemo, useState } from "react";
import clsx from "clsx";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { Activity, ArrowLeft } from "lucide-react";
import { useHasRole } from "@/components/RoleGate";
import {
  useEmployeeAverages,
  useProductivityDailySummary,
  useProductivityMonthlySummary,
  useProductivityThreeMonthSummary,
  useProductivityWeeklySummary,
  useWarehouses,
} from "@/api/hooks";
import type { WarehouseProductivity } from "@/api/types";
import { EmployeeAveragesTable } from "@/components/productivity/EmployeeAveragesTable";
import { PerformersTable } from "@/components/productivity/PerformersTable";
import { RosterEntryGrid } from "@/components/productivity/RosterEntryGrid";
import { isoWeekStart, todayStr } from "@/components/productivity/dates";

type Tab = "today" | "week" | "month" | "three-month";

export function WarehouseProductivityDetailPage() {
  const { warehouseId: warehouseIdParam } = useParams();
  const warehouseId = Number(warehouseIdParam);
  const [searchParams, setSearchParams] = useSearchParams();

  const initialDate = searchParams.get("date") || todayStr();
  const initialTab = (searchParams.get("tab") as Tab | null) ?? "today";

  const [date, setDate] = useState<string>(initialDate);
  const [tab, setTab] = useState<Tab>(
    ["today", "week", "month", "three-month"].includes(initialTab)
      ? initialTab
      : "today",
  );

  const canWrite = useHasRole(["admin", "operator"]);
  const { data: warehouses } = useWarehouses(true);
  const warehouse = useMemo(
    () => warehouses?.find((w) => w.id === warehouseId),
    [warehouses, warehouseId],
  );

  const dailyQuery = useProductivityDailySummary(warehouseId, date);
  const weeklyQuery = useProductivityWeeklySummary(
    warehouseId,
    isoWeekStart(date),
  );
  const monthlyQuery = useProductivityMonthlySummary(warehouseId, date);
  const threeMonthQuery = useProductivityThreeMonthSummary(warehouseId, date);
  const averagesQuery = useEmployeeAverages(warehouseId, date);

  const activeQuery =
    tab === "today"
      ? dailyQuery
      : tab === "week"
        ? weeklyQuery
        : tab === "month"
          ? monthlyQuery
          : threeMonthQuery;
  const summary: WarehouseProductivity | undefined =
    activeQuery.data?.warehouses.find((w) => w.warehouse_id === warehouseId);

  // Keep the query string in sync so refreshing the page or sharing the URL
  // lands you on the same date + tab you were looking at.
  const updateParams = (next: { date?: string; tab?: Tab }) => {
    const merged = new URLSearchParams(searchParams);
    if (next.date) merged.set("date", next.date);
    if (next.tab) merged.set("tab", next.tab);
    setSearchParams(merged, { replace: true });
  };

  if (Number.isNaN(warehouseId)) {
    return <Navigate to="/productivity" replace />;
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <Link
            to="/productivity"
            className="inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-800"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            All warehouses
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            {warehouse?.name ?? `Warehouse #${warehouseId}`}
          </h1>
          <p className="text-sm text-slate-500">
            Productivity entries and leaderboards for this warehouse.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <input
            type="date"
            className="input max-w-[180px]"
            value={date}
            onChange={(e) => {
              const next = e.target.value || todayStr();
              setDate(next);
              updateParams({ date: next });
            }}
            aria-label="Reporting date"
          />
          <span className="text-[11px] text-slate-400">
            Editing entries for this date
          </span>
        </div>
      </header>

      <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-1 text-sm font-medium">
        <TabButton
          active={tab === "today"}
          onClick={() => {
            setTab("today");
            updateParams({ tab: "today" });
          }}
        >
          Today
        </TabButton>
        <TabButton
          active={tab === "week"}
          onClick={() => {
            setTab("week");
            updateParams({ tab: "week" });
          }}
        >
          This week
        </TabButton>
        <TabButton
          active={tab === "month"}
          onClick={() => {
            setTab("month");
            updateParams({ tab: "month" });
          }}
        >
          This month
        </TabButton>
        <TabButton
          active={tab === "three-month"}
          onClick={() => {
            setTab("three-month");
            updateParams({ tab: "three-month" });
          }}
        >
          3 months
        </TabButton>
      </div>

      <section className="card overflow-hidden">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-brand-600" />
            <div>
              <h2 className="font-semibold">Summary</h2>
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

        <EmployeeAveragesTable
          data={averagesQuery.data}
          isLoading={averagesQuery.isLoading}
        />

        <RosterEntryGrid
          warehouseId={warehouseId}
          date={date}
          canWrite={canWrite}
        />
      </section>
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

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-xl font-semibold text-slate-900">{value}</dd>
    </div>
  );
}
