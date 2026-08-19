import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Pencil,
  Search,
  Trash2,
} from "lucide-react";
import { useLot, useLotBoxes, useLotEvents, usePallets, useWarehouses } from "@/api/hooks";
import {
  ALL_BOX_STATUSES,
  type BoxFilters,
  type BoxStatus,
  type LotDetail,
  type MergedLot,
} from "@/api/types";
import { LotStatusBar } from "@/components/LotStatusBar";
import { PurgeLotDialog } from "@/components/PurgeLotDialog";
import { RenameLotDialog } from "@/components/RenameLotDialog";
import { STATUS_LABEL, StatusBadge } from "@/components/StatusBadge";
import { useHasRole } from "@/components/RoleGate";
import { completionLabel } from "@/pages/lots";

export function LotDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const lotId = id ? Number(id) : undefined;
  const lot = useLot(lotId);
  const auditEvents = useLotEvents(lotId);
  const warehouses = useWarehouses(true);
  const [params, setParams] = useSearchParams();
  const isAdmin = useHasRole(["admin"]);
  const [showRename, setShowRename] = useState(false);
  const [showPurge, setShowPurge] = useState(false);
  const page = Math.max(1, Number(params.get("page")) || 1);
  const pageSize = 25;
  const filters = useMemo<BoxFilters>(() => {
    const next: BoxFilters = {};
    const search = params.get("q");
    const status = params.get("status");
    const warehouseId = Number(params.get("warehouse_id"));
    if (search) next.search = search;
    if (ALL_BOX_STATUSES.includes(status as BoxStatus)) {
      next.status = status as BoxStatus;
    }
    if (warehouseId > 0) next.warehouse_id = warehouseId;
    next.sort_by = "updated_at";
    next.sort_dir = "desc";
    return next;
  }, [params]);
  const boxes = useLotBoxes(lotId, filters, page, pageSize);
  const pallets = usePallets(
    { lot_id: lotId, sort_by: "pallet_number", sort_dir: "asc" },
    1,
    200,
  );
  const totalPages = Math.max(
    1,
    Math.ceil((boxes.data?.total ?? 0) / pageSize),
  );
  const mergedTarget =
    lot.data && isMergedLot(lot.data)
      ? lot.data.merged_into
      : null;

  useEffect(() => {
    if (mergedTarget) {
      navigate(`/lots/${mergedTarget.id}`, { replace: true });
    }
  }, [mergedTarget, navigate]);

  function setParam(name: string, value?: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    next.delete("page");
    setParams(next);
  }

  if (lot.isLoading) return <p className="text-sm text-slate-500">Loading lot…</p>;
  if (lot.isError || !lot.data) {
    return (
      <div className="card card-pad text-sm text-rose-700" role="alert">
        This lot could not be loaded. <Link className="underline" to="/lots">Back to lots</Link>
      </div>
    );
  }
  if (isMergedLot(lot.data)) {
    return (
      <p className="text-sm text-slate-500">
        This lot was merged into {lot.data.merged_into.name}. Redirecting…
      </p>
    );
  }
  const summary = lot.data;

  return (
    <div className="space-y-6">
      <Link
        to="/lots"
        className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
      >
        <ArrowLeft className="h-4 w-4" /> All lots
      </Link>

      <header className="card card-pad space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight">
                {summary.name}
              </h1>
              {summary.acl_scoped && (
                <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800">
                  Accessible warehouses only
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-slate-500">
              Created {new Date(summary.created_at).toLocaleString()} · Version{" "}
              {summary.version}
            </p>
          </div>
          {isAdmin && (
            <button className="btn-secondary" onClick={() => setShowRename(true)}>
              <Pencil className="h-4 w-4" /> Correct name
            </button>
          )}
        </div>

        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Metric label="Total boxes" value={summary.box_count} />
          <Metric label="Eligible" value={summary.eligible_box_count} />
          <Metric label="Completed" value={summary.completed_box_count} />
          <Metric label="Quarantined" value={summary.status_counts.quarantined} />
          <Metric label="Staged receipts" value={summary.staged_receipt_count} />
        </dl>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div>
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
              Status distribution
            </h2>
            <LotStatusBar counts={summary.status_counts} />
          </div>
          <div className="rounded-lg bg-slate-50 p-3">
            <div className="text-lg font-semibold tabular-nums">
              {completionLabel(summary.completion_percent)}
            </div>
            <p className="mt-1 text-xs text-slate-600">
              Completion is completed ÷ eligible boxes. Completed includes
              incomplete, ready-to-return, and returned boxes. Quarantined
              boxes are excluded.
            </p>
          </div>
        </div>

        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <Metadata
            label="Warehouses"
            value={summary.warehouse_names.join(", ") || "No visible warehouses"}
          />
          <Metadata
            label="Last box activity"
            value={
              summary.last_box_activity
                ? new Date(summary.last_box_activity).toLocaleString()
                : "No box activity"
            }
          />
        </dl>
      </header>

      <section className="card overflow-hidden">
        <header className="border-b border-slate-100 p-4">
          <h2 className="font-semibold">Pallets</h2>
          <p className="text-xs text-slate-500">
            Pallets are shown before individual boxes. Boxes without a pallet
            remain visible in the explicit Unassigned group below.
          </p>
        </header>
        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
          {pallets.data?.items.map((pallet) => (
            <Link key={pallet.id} to={`/pallets/${pallet.id}`} className="rounded-lg border border-slate-200 p-3 hover:border-brand-300 hover:bg-brand-50">
              <div className="flex items-center justify-between gap-2">
                <strong className="text-brand-700">{pallet.pallet_number}</strong>
                <span className="text-xs text-slate-500">{pallet.box_count} boxes</span>
              </div>
              <p className="mt-1 text-xs text-slate-600">{pallet.completion_percent === null ? "N/A" : `${Math.round(pallet.completion_percent)}%`} complete</p>
              <p className="mt-1 text-xs text-slate-500">{pallet.warehouse_names.length ? `Boxes currently in ${pallet.warehouse_names.join(", ")}` : "No boxes in accessible warehouses"}</p>
            </Link>
          ))}
          <Link to={`/boxes?lot_id=${summary.id}&lot_name=${encodeURIComponent(summary.name)}&unassigned_pallet=true`} className="rounded-lg border border-dashed border-slate-300 p-3 hover:bg-slate-50">
            <div className="flex items-center justify-between gap-2">
              <strong>Unassigned boxes</strong>
              <span className="text-xs text-slate-500">
                {Math.max(0, summary.box_count - (pallets.data?.items.reduce((total, pallet) => total + pallet.box_count, 0) ?? 0))}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-500">View all lot boxes; unassigned rows are labelled below.</p>
          </Link>
        </div>
      </section>

      <section className="card overflow-hidden">
        <div className="border-b border-slate-100 p-4">
          <h2 className="font-semibold">Contained boxes</h2>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            <label className="block">
              <span className="sr-only">Search boxes</span>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-slate-400" />
                <input
                  className="input pl-8"
                  placeholder="Box number, pallet, or item descriptions"
                  value={filters.search ?? ""}
                  onChange={(event) => setParam("q", event.target.value)}
                />
              </div>
            </label>
            <select
              className="input"
              aria-label="Filter by status"
              value={filters.status ?? ""}
              onChange={(event) => setParam("status", event.target.value)}
            >
              <option value="">All statuses</option>
              {ALL_BOX_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABEL[status]}
                </option>
              ))}
            </select>
            <select
              className="input"
              aria-label="Filter by warehouse"
              value={filters.warehouse_id ?? ""}
              onChange={(event) => setParam("warehouse_id", event.target.value)}
            >
              <option value="">All visible warehouses</option>
              {warehouses.data?.map((warehouse) => (
                <option key={warehouse.id} value={warehouse.id}>
                  {warehouse.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        {boxes.isLoading && <p className="p-5 text-sm text-slate-500">Loading boxes…</p>}
        {boxes.isError && (
          <p className="p-5 text-sm text-rose-600" role="alert">
            Boxes could not be loaded.
          </p>
        )}
        {!boxes.isLoading && boxes.data?.items.length === 0 && (
          <p className="p-8 text-center text-sm text-slate-500">
            No boxes match these filters.
          </p>
        )}
        {!!boxes.data?.items.length && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-4 py-2.5">Box</th>
                  <th className="px-4 py-2.5">Status</th>
                  <th className="px-4 py-2.5">Warehouse</th>
                  <th className="px-4 py-2.5">Pallet</th>
                  <th className="px-4 py-2.5">Item descriptions</th>
                  <th className="px-4 py-2.5">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {boxes.data.items.map((box) => (
                  <tr key={box.id}>
                    <td className="px-4 py-3 font-mono">
                      <Link className="text-brand-700 hover:underline" to={`/boxes/${box.id}`}>
                        {box.box_number}
                      </Link>
                    </td>
                    <td className="px-4 py-3"><StatusBadge status={box.status} /></td>
                    <td className="px-4 py-3">
                      {warehouses.data?.find((warehouse) => warehouse.id === box.current_warehouse_id)?.name ?? `#${box.current_warehouse_id}`}
                    </td>
                    <td className="px-4 py-3">
                      {box.pallet_id ? <Link className="text-brand-700 hover:underline" to={`/pallets/${box.pallet_id}`}>{box.pallet_number ?? `#${box.pallet_id}`}</Link> : <span className="font-medium text-amber-700">Unassigned</span>}
                    </td>
                    <td className="max-w-80 truncate px-4 py-3 text-slate-600">
                      {box.contents || "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-500">
                      {new Date(box.updated_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
          <span>Page {page} of {totalPages} · {boxes.data?.total ?? 0} boxes</span>
          <div className="flex gap-1">
            <button className="btn-ghost" disabled={page <= 1} onClick={() => {
              const next = new URLSearchParams(params); next.set("page", String(page - 1)); setParams(next);
            }}><ChevronLeft className="h-4 w-4" /> Prev</button>
            <button className="btn-ghost" disabled={page >= totalPages} onClick={() => {
              const next = new URLSearchParams(params); next.set("page", String(page + 1)); setParams(next);
            }}>Next <ChevronRight className="h-4 w-4" /></button>
          </div>
        </div>
      </section>

      <section id="lot-audit-history" className="card card-pad">
        <h2 className="font-semibold">Lot audit history</h2>
        {!summary.audit_history_included ? (
          <p className="mt-2 text-sm text-slate-500">
            Audit history is available to administrators.
          </p>
        ) : auditEvents.data?.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No lot audit events yet.</p>
        ) : (
          <ol className="mt-4 space-y-4">
            {auditEvents.data?.map((event) => (
              <li key={event.id} className="border-l-2 border-brand-200 pl-3 text-sm">
                <p className="font-medium">
                  {event.event_type === "merged"
                    ? event.metadata.event_side === "source"
                      ? `Merged into “${String(event.metadata.target_lot_name ?? event.new_name ?? "")}”`
                      : `Received merged lot “${String(event.metadata.source_lot_name ?? event.old_name ?? "")}”`
                    : event.event_type.replaceAll("_", " ")}
                </p>
                {event.old_name && event.new_name && (
                  <p className="text-slate-600">“{event.old_name}” → “{event.new_name}”</p>
                )}
                {event.reason && <p className="text-slate-600">Reason: {event.reason}</p>}
                <p className="text-xs text-slate-500">
                  {new Date(event.occurred_at).toLocaleString()}
                  {event.actor_user_id ? ` · User #${event.actor_user_id}` : ""}
                </p>
              </li>
            ))}
          </ol>
        )}
      </section>

      {isAdmin && (
        <section className="rounded-xl border border-rose-200 bg-rose-50 p-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="font-semibold text-rose-950">Danger zone</h2>
              <p className="mt-1 max-w-2xl text-sm text-rose-800">
                Safe purge applies to archived boxes and exclusive
                self-receipts. Eligibility is checked first; a separate
                administrative recovery review appears only when safe purge is
                blocked.
              </p>
            </div>
            <button
              type="button"
              className="btn-danger shrink-0"
              onClick={() => setShowPurge(true)}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Purge lot
            </button>
          </div>
        </section>
      )}

      {showRename && (
        <RenameLotDialog
          lot={summary}
          onClose={() => setShowRename(false)}
          onMerged={(targetLotId) => navigate(`/lots/${targetLotId}`, { replace: true })}
        />
      )}
      {isAdmin && showPurge && (
        <PurgeLotDialog
          lot={summary}
          onClose={() => setShowPurge(false)}
          onNavigateToLots={() => navigate("/lots", { replace: true })}
        />
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div className="rounded-lg bg-slate-50 p-3"><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 text-xl font-semibold tabular-nums">{value}</dd></div>;
}

function Metadata({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs uppercase tracking-wider text-slate-400">{label}</dt><dd className="mt-0.5 text-slate-700">{value}</dd></div>;
}

function isMergedLot(lot: LotDetail | MergedLot): lot is MergedLot {
  return "state" in lot && lot.state === "merged";
}

