import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Plus, Search } from "lucide-react";
import { useCreatePallet, usePallets, useWarehouses } from "@/api/hooks";
import type { PalletSortField } from "@/api/types";
import { LotPicker, type LotSelection } from "@/components/LotPicker";
import { LotStatusBar } from "@/components/LotStatusBar";
import { useHasRole } from "@/components/RoleGate";
import {
  PALLET_PROGRESS_LABELS,
  palletCompletionLabel,
  parsePalletSearchParams,
} from "@/pages/pallets";

const SORTS: { value: PalletSortField; label: string }[] = [
  { value: "latest_activity", label: "Latest activity" },
  { value: "pallet_number", label: "Pallet number" },
  { value: "completion", label: "Completion" },
  { value: "box_count", label: "Box count" },
];

export function PalletsPage() {
  const [params, setParams] = useSearchParams();
  const parsed = useMemo(() => parsePalletSearchParams(params), [params]);
  const pallets = usePallets(parsed.filters, parsed.page, parsed.pageSize);
  const canWrite = useHasRole(["admin", "operator"]);
  const isAdmin = useHasRole(["admin"]);
  const [creating, setCreating] = useState(false);
  const totalPages = Math.max(
    1,
    Math.ceil((pallets.data?.total ?? 0) / parsed.pageSize),
  );

  function setParam(name: string, value?: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    next.delete("page");
    setParams(next);
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Pallets</h1>
          <p className="text-sm text-slate-500">
            Track pallet organization and box progress across warehouses.
          </p>
        </div>
        {canWrite && (
          <button className="btn-primary" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> Create pallet
          </button>
        )}
      </header>
      {!isAdmin && (
        <p className="rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm text-sky-900">
          Box counts and warehouse distributions include accessible warehouses only.
        </p>
      )}
      <section className="card card-pad grid gap-3 md:grid-cols-2 xl:grid-cols-6">
        <label className="block xl:col-span-2">
          <span className="text-xs text-slate-500">Search</span>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-slate-400" />
            <input className="input pl-8" placeholder="Pallet number or lot" value={parsed.filters.search ?? ""} onChange={(e) => setParam("q", e.target.value)} />
          </div>
        </label>
        <LotPicker
          label="Lot"
          value={
            parsed.filters.lot_id
              ? {
                  id: parsed.filters.lot_id,
                  name: params.get("lot_name") ?? `Lot #${parsed.filters.lot_id}`,
                }
              : null
          }
          onChange={(selection) => {
            const next = new URLSearchParams(params);
            next.delete("page");
            if (selection) {
              next.set("lot_id", String(selection.id));
              next.set("lot_name", selection.name);
            } else {
              next.delete("lot_id");
              next.delete("lot_name");
            }
            setParams(next);
          }}
        />
        <label className="block">
          <span className="text-xs text-slate-500">Progress</span>
          <select className="input" value={parsed.filters.progress_state ?? ""} onChange={(e) => setParam("progress", e.target.value)}>
            <option value="">All</option>
            {Object.entries(PALLET_PROGRESS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Sort</span>
          <select className="input" value={parsed.filters.sort_by ?? "latest_activity"} onChange={(e) => setParam("sort_by", e.target.value)}>
            {SORTS.map((sort) => <option key={sort.value} value={sort.value}>{sort.label}</option>)}
          </select>
        </label>
        <div className="flex items-end gap-2">
          <button className="btn-secondary" aria-label="Toggle sort direction" onClick={() => setParam("sort_dir", parsed.filters.sort_dir === "asc" ? "desc" : "asc")}>
            {parsed.filters.sort_dir === "asc" ? <ArrowUp className="h-4 w-4" /> : <ArrowDown className="h-4 w-4" />}
          </button>
          {isAdmin && (
            <label className="flex min-h-10 items-center gap-2 text-xs text-slate-700">
              <input type="checkbox" checked={!!parsed.filters.include_inactive} onChange={(e) => setParam("include_inactive", e.target.checked ? "true" : undefined)} />
              Archived
            </label>
          )}
        </div>
      </section>
      {pallets.isLoading && <div className="card card-pad text-sm text-slate-500">Loading pallets…</div>}
      {pallets.isError && <div className="card card-pad text-sm text-rose-700" role="alert">Pallets could not be loaded.</div>}
      {!pallets.isLoading && pallets.data?.items.length === 0 && <div className="card card-pad py-12 text-center text-sm text-slate-500">No pallets match these filters.</div>}
      {!!pallets.data?.items.length && (
        <section className="card overflow-hidden">
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500"><tr><th className="px-4 py-3">Pallet</th><th className="px-4 py-3">Lot</th><th className="px-4 py-3">Boxes</th><th className="min-w-52 px-4 py-3">Status distribution</th><th className="px-4 py-3">Completion</th><th className="px-4 py-3">Activity</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {pallets.data.items.map((pallet) => (
                  <tr key={pallet.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3"><Link className="font-medium text-brand-700 hover:underline" to={`/pallets/${pallet.id}`}>{pallet.pallet_number}</Link>{!pallet.is_active && <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs">Archived</span>}<p className="mt-1 text-xs text-slate-500">{pallet.warehouse_names.length ? `Boxes currently in ${pallet.warehouse_names.join(", ")}` : "No boxes in accessible warehouses"}</p></td>
                    <td className="px-4 py-3"><Link className="text-brand-700 hover:underline" to={`/lots/${pallet.lot_id}`}>{pallet.lot_name}</Link></td>
                    <td className="px-4 py-3 tabular-nums">{pallet.box_count}</td>
                    <td className="px-4 py-3"><LotStatusBar counts={pallet.status_counts} /></td>
                    <td className="px-4 py-3 font-medium">{palletCompletionLabel(pallet.completion_percent)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-slate-500">{new Date(pallet.latest_activity).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="divide-y divide-slate-100 md:hidden">
            {pallets.data.items.map((pallet) => (
              <article key={pallet.id} className="space-y-2 p-4">
                <div className="flex justify-between gap-3"><div><Link className="font-semibold text-brand-700" to={`/pallets/${pallet.id}`}>{pallet.pallet_number}</Link><p className="text-xs text-slate-500">{pallet.lot_name}</p><p className="text-xs text-slate-500">{pallet.warehouse_names.length ? `Boxes currently in ${pallet.warehouse_names.join(", ")}` : "No boxes in accessible warehouses"}</p></div><strong>{palletCompletionLabel(pallet.completion_percent)}</strong></div>
                <LotStatusBar counts={pallet.status_counts} />
                <p className="text-xs text-slate-600">{pallet.box_count} boxes · {pallet.completed_box_count} completed{!pallet.is_active ? " · Archived" : ""}</p>
              </article>
            ))}
          </div>
          <div className="flex items-center justify-between border-t px-4 py-3 text-xs text-slate-500">
            <span>Page {parsed.page} of {totalPages} · {pallets.data.total} total</span>
            <div className="flex gap-1">
              <button className="btn-ghost" disabled={parsed.page <= 1} onClick={() => { const next = new URLSearchParams(params); next.set("page", String(parsed.page - 1)); setParams(next); }}><ChevronLeft className="h-4 w-4" /> Prev</button>
              <button className="btn-ghost" disabled={parsed.page >= totalPages} onClick={() => { const next = new URLSearchParams(params); next.set("page", String(parsed.page + 1)); setParams(next); }}>Next <ChevronRight className="h-4 w-4" /></button>
            </div>
          </div>
        </section>
      )}
      {creating && <CreatePalletDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function CreatePalletDialog({ onClose }: { onClose: () => void }) {
  const warehouses = useWarehouses();
  const create = useCreatePallet();
  const [lot, setLot] = useState<LotSelection | null>(null);
  const [number, setNumber] = useState("");
  const [error, setError] = useState<string | null>(null);
  const authorizationWarehouseId = warehouses.data?.find(
    (warehouse) => warehouse.is_active,
  )?.id;
  return (
    <div className="modal-backdrop z-40">
      <form className="modal-sheet max-w-md space-y-3" onSubmit={async (event) => {
        event.preventDefault();
        if (!lot) return;
        if (!authorizationWarehouseId) {
          setError("Pallet creation needs access to at least one active warehouse for authorization. This does not set a pallet location.");
          return;
        }
        try {
          await create.mutateAsync({ warehouse_id: authorizationWarehouseId, lot_id: lot.id, pallet_number: number.trim() });
          onClose();
        } catch (caught) {
          const detail = (caught as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
          const message = typeof detail === "string" ? detail : "The pallet could not be created.";
          setError(`${message} An active accessible warehouse was supplied only as authorization context; it does not locate the pallet.`);
        }
      }}>
        <h2 className="text-lg font-semibold">Create pallet</h2>
        <LotPicker value={lot} onChange={setLot} warehouseId={authorizationWarehouseId} canCreate />
        <label className="block"><span className="text-xs text-slate-500">Pallet number</span><input required maxLength={64} className="input" value={number} onChange={(e) => setNumber(e.target.value)} /></label>
        {error && <p role="alert" className="text-sm text-rose-600">{error}</p>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={create.isPending || !lot || !number.trim()}>{create.isPending ? "Creating…" : "Create pallet"}</button></div>
      </form>
    </div>
  );
}
