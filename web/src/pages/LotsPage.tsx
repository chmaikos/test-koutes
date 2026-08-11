import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Pencil,
  Search,
} from "lucide-react";
import { useLots, useWarehouses } from "@/api/hooks";
import type { LotSortField, LotSummary } from "@/api/types";
import { LotStatusBar } from "@/components/LotStatusBar";
import { RenameLotDialog } from "@/components/RenameLotDialog";
import { useHasRole } from "@/components/RoleGate";
import {
  completionLabel,
  LOT_PROGRESS_LABELS,
  parseLotSearchParams,
} from "@/pages/lots";

const SORT_OPTIONS: { value: LotSortField; label: string }[] = [
  { value: "last_activity", label: "Last activity" },
  { value: "name", label: "Name" },
  { value: "completion", label: "Completion" },
  { value: "box_count", label: "Box count" },
];

export function LotsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const parsed = useMemo(() => parseLotSearchParams(params), [params]);
  const lots = useLots(parsed.filters, parsed.page, parsed.pageSize);
  const warehouses = useWarehouses(true);
  const isAdmin = useHasRole(["admin"]);
  const [renaming, setRenaming] = useState<LotSummary | null>(null);
  const totalPages = Math.max(
    1,
    Math.ceil((lots.data?.total ?? 0) / parsed.pageSize),
  );

  function setParam(name: string, value?: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    next.delete("page");
    setParams(next);
  }

  const sortBy = parsed.filters.sort_by ?? "last_activity";
  const sortDir = parsed.filters.sort_dir ?? "desc";

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Lots</h1>
        <p className="text-sm text-slate-500">
          Track completion, inventory distribution, and recent activity by lot.
        </p>
      </header>

      {!isAdmin && (
        <div className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900">
          Metrics are scoped to your accessible warehouses. Totals may differ
          from organization-wide lot totals.
        </div>
      )}

      <section className="card card-pad grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <label className="block xl:col-span-2">
          <span className="text-xs text-slate-500">Search</span>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-slate-400" />
            <input
              className="input pl-8"
              placeholder="Lot name"
              value={parsed.filters.search ?? ""}
              onChange={(event) => setParam("q", event.target.value)}
            />
          </div>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Warehouse</span>
          <select
            className="input"
            value={parsed.filters.warehouse_id ?? ""}
            onChange={(event) => setParam("warehouse_id", event.target.value)}
          >
            <option value="">All accessible</option>
            {warehouses.data?.map((warehouse) => (
              <option key={warehouse.id} value={warehouse.id}>
                {warehouse.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Progress</span>
          <select
            className="input"
            value={parsed.filters.progress_state ?? ""}
            onChange={(event) => setParam("progress", event.target.value)}
          >
            <option value="">All</option>
            {Object.entries(LOT_PROGRESS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <div className="grid grid-cols-[1fr_auto] gap-2">
          <label className="block">
            <span className="text-xs text-slate-500">Sort</span>
            <select
              className="input"
              value={sortBy}
              onChange={(event) => {
                const next = new URLSearchParams(params);
                next.set("sort_by", event.target.value);
                next.set("sort_dir", sortDir);
                next.delete("page");
                setParams(next);
              }}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn-secondary mt-5 px-2.5"
            aria-label={`Sort ${sortDir === "asc" ? "descending" : "ascending"}`}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("sort_by", sortBy);
              next.set("sort_dir", sortDir === "asc" ? "desc" : "asc");
              next.delete("page");
              setParams(next);
            }}
          >
            {sortDir === "asc" ? (
              <ArrowUp className="h-4 w-4" />
            ) : (
              <ArrowDown className="h-4 w-4" />
            )}
          </button>
        </div>
      </section>

      {lots.isError && (
        <div className="card card-pad text-sm text-rose-700" role="alert">
          Lots could not be loaded.{" "}
          <button className="underline" onClick={() => void lots.refetch()}>
            Try again
          </button>
        </div>
      )}
      {lots.isLoading && (
        <div className="card card-pad text-sm text-slate-500">Loading lots…</div>
      )}
      {!lots.isLoading && !lots.isError && lots.data?.items.length === 0 && (
        <div className="card card-pad py-12 text-center">
          <h2 className="font-medium text-slate-800">No lots found</h2>
          <p className="mt-1 text-sm text-slate-500">
            Adjust the search or filters to broaden the results.
          </p>
        </div>
      )}

      {!!lots.data?.items.length && (
        <div className="card overflow-hidden">
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-4 py-3">Lot</th>
                  <th className="px-4 py-3">Boxes</th>
                  <th className="min-w-56 px-4 py-3">Status distribution</th>
                  <th className="px-4 py-3">Completion</th>
                  <th className="px-4 py-3">Warehouses</th>
                  <th className="px-4 py-3">Last activity</th>
                  {isAdmin && <th className="px-4 py-3 text-right">Admin</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {lots.data.items.map((lot) => (
                  <tr key={lot.id} className="align-top hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <Link
                        to={`/lots/${lot.id}`}
                        className="font-medium text-brand-700 hover:underline"
                      >
                        {lot.name}
                      </Link>
                      {lot.staged_receipt_count > 0 && (
                        <div className="mt-1 text-xs text-amber-700">
                          {lot.staged_receipt_count} staged receipt
                          {lot.staged_receipt_count === 1 ? "" : "s"}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 tabular-nums">
                      <strong>{lot.box_count}</strong> total
                      <div className="text-xs text-slate-500">
                        {lot.eligible_box_count} eligible ·{" "}
                        {lot.completed_box_count} completed ·{" "}
                        {lot.status_counts.quarantined} quarantined
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <LotStatusBar counts={lot.status_counts} />
                    </td>
                    <td className="px-4 py-3">
                      <Completion lot={lot} />
                    </td>
                    <td className="max-w-56 px-4 py-3 text-slate-600">
                      {lot.warehouse_names.join(", ") || "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-500">
                      {formatDate(lot.last_box_activity)}
                    </td>
                    {isAdmin && (
                      <td className="px-4 py-3 text-right">
                        <button
                          type="button"
                          className="btn-ghost text-xs"
                          onClick={() => setRenaming(lot)}
                        >
                          <Pencil className="h-3.5 w-3.5" /> Rename
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="divide-y divide-slate-100 md:hidden">
            {lots.data.items.map((lot) => (
              <article key={lot.id} className="space-y-3 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <Link
                      to={`/lots/${lot.id}`}
                      className="font-semibold text-brand-700"
                    >
                      {lot.name}
                    </Link>
                    <p className="text-xs text-slate-500">
                      {lot.box_count} boxes · {lot.warehouse_names.join(", ") || "No warehouse"}
                    </p>
                  </div>
                  <Completion lot={lot} compact />
                </div>
                <LotStatusBar counts={lot.status_counts} />
                <p className="text-xs text-slate-600">
                  {lot.eligible_box_count} eligible · {lot.completed_box_count}{" "}
                  completed · {lot.status_counts.quarantined} quarantined
                  {lot.staged_receipt_count
                    ? ` · ${lot.staged_receipt_count} staged`
                    : ""}
                </p>
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span>Activity {formatDate(lot.last_box_activity)}</span>
                  {isAdmin && (
                    <button
                      type="button"
                      className="text-brand-700"
                      onClick={() => setRenaming(lot)}
                    >
                      Rename
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
          <Pagination
            page={parsed.page}
            totalPages={totalPages}
            total={lots.data.total}
            pageSize={parsed.pageSize}
            params={params}
            setParams={setParams}
          />
        </div>
      )}

      {renaming && (
        <RenameLotDialog
          lot={renaming}
          onClose={() => setRenaming(null)}
          onMerged={(targetLotId) => navigate(`/lots/${targetLotId}`)}
        />
      )}
    </div>
  );
}

function Completion({ lot, compact = false }: { lot: LotSummary; compact?: boolean }) {
  return (
    <div className={compact ? "text-right" : "min-w-24"}>
      <strong className="tabular-nums">{completionLabel(lot.completion_percent)}</strong>
      {lot.completion_percent !== null && (
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
          <span
            className="block h-full rounded-full bg-emerald-500"
            style={{ width: `${Math.min(100, lot.completion_percent)}%` }}
          />
        </div>
      )}
    </div>
  );
}

function Pagination({
  page,
  totalPages,
  total,
  pageSize,
  params,
  setParams,
}: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  params: URLSearchParams;
  setParams: ReturnType<typeof useSearchParams>[1];
}) {
  function go(nextPage: number) {
    const next = new URLSearchParams(params);
    next.set("page", String(nextPage));
    setParams(next);
  }
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
      <span>
        Page {page} of {totalPages} · {total} total
      </span>
      <div className="flex items-center gap-2">
        <select
          className="input h-8 w-auto py-0 text-xs"
          aria-label="Lots per page"
          value={pageSize}
          onChange={(event) => {
            const next = new URLSearchParams(params);
            const value = Number(event.target.value);
            if (value === 25) next.delete("page_size");
            else next.set("page_size", String(value));
            next.delete("page");
            setParams(next);
          }}
        >
          {[25, 50, 100, 200].map((size) => (
            <option key={size} value={size}>
              {size} per page
            </option>
          ))}
        </select>
        <button className="btn-ghost" disabled={page <= 1} onClick={() => go(page - 1)}>
          <ChevronLeft className="h-4 w-4" /> Prev
        </button>
        <button
          className="btn-ghost"
          disabled={page >= totalPages}
          onClick={() => go(page + 1)}
        >
          Next <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "No activity";
}

