import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Archive, Pencil, RotateCcw, Search } from "lucide-react";
import {
  useArchivePallet,
  useAssignPalletBoxes,
  useBoxes,
  useDetachPalletBoxes,
  usePallet,
  usePalletEvents,
  useRenamePallet,
  useRestorePallet,
  useWarehouses,
} from "@/api/hooks";
import { ALL_BOX_STATUSES, type BoxStatus, type Warehouse } from "@/api/types";
import { BarcodeDisplay } from "@/components/BarcodeDisplay";
import { LotStatusBar } from "@/components/LotStatusBar";
import {
  PrintableLabelDialog,
  PrintLabelButton,
} from "@/components/PrintableLabelDialog";
import { STATUS_LABEL, StatusBadge } from "@/components/StatusBadge";
import { useHasRole } from "@/components/RoleGate";
import { palletCandidateFilters, palletCompletionLabel } from "@/pages/pallets";

export function PalletDetailPage() {
  const { id } = useParams<{ id: string }>();
  const palletId = id ? Number(id) : undefined;
  const isAdmin = useHasRole(["admin"]);
  const canWrite = useHasRole(["admin", "operator"]);
  const pallet = usePallet(palletId, isAdmin);
  const events = usePalletEvents(palletId, isAdmin);
  const warehouses = useWarehouses(true);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<BoxStatus | "">("");
  const [boxPage, setBoxPage] = useState(1);
  const [boxWarehouseId, setBoxWarehouseId] = useState<number | "">("");
  const [action, setAction] = useState<"rename" | "archive" | "restore" | null>(null);
  const [reason, setReason] = useState("");
  const [newNumber, setNewNumber] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [printLabel, setPrintLabel] = useState(false);
  const rename = useRenamePallet();
  const archive = useArchivePallet();
  const restore = useRestorePallet();

  const boxFilters = useMemo(
    () => ({
      pallet_id: palletId,
      search: search || undefined,
      status: status || undefined,
      warehouse_id: boxWarehouseId || undefined,
      sort_by: "updated_at" as const,
      sort_dir: "desc" as const,
    }),
    [boxWarehouseId, palletId, search, status],
  );
  const boxPageSize = 50;
  const boxes = useBoxes(boxFilters, boxPage, boxPageSize);
  const boxPageCount = Math.max(
    1,
    Math.ceil((boxes.data?.total ?? 0) / boxPageSize),
  );

  if (pallet.isLoading) return <p className="text-sm text-slate-500">Loading pallet…</p>;
  if (!pallet.data) return <div role="alert" className="card card-pad text-sm text-rose-700">This pallet could not be loaded. <Link className="underline" to="/pallets">Back to pallets</Link></div>;
  const summary = pallet.data;
  const pending = rename.isPending || archive.isPending || restore.isPending;

  async function submitAction(event: React.FormEvent) {
    event.preventDefault();
    if (!action) return;
    setFeedback(null);
    try {
      if (action === "rename") {
        await rename.mutateAsync({ id: summary.id, payload: { new_pallet_number: newNumber.trim(), reason: reason.trim(), expected_version: summary.version } });
      } else if (action === "archive") {
        await archive.mutateAsync({ id: summary.id, payload: { reason: reason.trim(), expected_version: summary.version } });
      } else if (action === "restore") {
        await restore.mutateAsync({ id: summary.id, payload: { reason: reason.trim(), expected_version: summary.version } });
      }
      setAction(null);
      setReason("");
      setNewNumber("");
    } catch (caught) {
      const detail = (caught as { response?: { data?: { detail?: string | { message?: string } } } }).response?.data?.detail;
      setFeedback(typeof detail === "object" ? detail.message ?? "The pallet changed; latest state is loading." : detail ?? "The pallet action failed.");
    }
  }

  return (
    <div className="space-y-6">
      <Link to="/pallets" className="inline-flex items-center gap-1 text-sm text-slate-500"><ArrowLeft className="h-4 w-4" /> All pallets</Link>
      <header className="card card-pad space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2"><h1 className="text-2xl font-semibold">{summary.pallet_number}</h1>{!summary.is_active && <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs">Archived</span>}</div>
            <p className="text-sm text-slate-500"><Link className="text-brand-700 hover:underline" to={`/lots/${summary.lot_id}`}>{summary.lot_name}</Link> · version {summary.version}</p>
            <p className="mt-1 text-xs text-slate-500">{summary.warehouse_names.length ? `Boxes currently in ${summary.warehouse_names.join(", ")}` : "No boxes in accessible warehouses"}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <PrintLabelButton onClick={() => setPrintLabel(true)} />
            {canWrite && (
              <>
              {isAdmin && <button className="btn-secondary" onClick={() => { setAction("rename"); setNewNumber(summary.pallet_number); }}><Pencil className="h-4 w-4" /> Rename</button>}
              {isAdmin && summary.is_active && <button className="btn-secondary" onClick={() => setAction("archive")}><Archive className="h-4 w-4" /> Archive</button>}
              {isAdmin && !summary.is_active && summary.absorbed_into_pallet_id === null && <button className="btn-secondary" onClick={() => setAction("restore")}><RotateCcw className="h-4 w-4" /> Restore</button>}
              </>
            )}
          </div>
        </div>
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-400">Generated immutable barcode</p>
          <BarcodeDisplay value={summary.barcode} />
        </div>
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
          <Metric label="Boxes" value={summary.box_count} />
          <Metric label="Active Files" value={summary.active_file_count} />
          <Metric label="Eligible" value={summary.eligible_box_count} />
          <Metric label="Completed" value={summary.completed_box_count} />
          <Metric label="Quarantined" value={summary.status_counts.quarantined} />
          <Metric label="Completion" value={palletCompletionLabel(summary.completion_percent)} />
        </dl>
        <Link className="text-sm text-brand-700 hover:underline" to={`/files?lot_id=${summary.lot_id}&pallet_id=${summary.id}&activity=all`}>
          Browse {summary.active_file_count} active and {summary.archived_file_count} archived physical Files
        </Link>
        <div><h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Status distribution</h2><LotStatusBar counts={summary.status_counts} /></div>
        {summary.archive_reason && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">Archive reason: {summary.archive_reason}</p>}
        {summary.absorbed_into_pallet_id !== null && <p className="rounded-lg bg-slate-100 p-3 text-sm text-slate-700">This pallet was absorbed during a lot merge and cannot be restored. Its surviving pallet is <Link className="text-brand-700 hover:underline" to={`/pallets/${summary.absorbed_into_pallet_id}`}>#{summary.absorbed_into_pallet_id}</Link>.</p>}
      </header>
      {printLabel && (
        <PrintableLabelDialog
          label={{
            entityType: "Pallet",
            title: summary.pallet_number,
            barcode: summary.barcode,
            context: [
              `Lot: ${summary.lot_name}`,
              `Warehouses: ${summary.warehouse_names.join(", ") || "No boxes in accessible warehouses"}`,
            ],
          }}
          onClose={() => setPrintLabel(false)}
        />
      )}

      {action && (
        <form className="card card-pad space-y-3" onSubmit={submitAction}>
          <h2 className="font-semibold capitalize">{action} pallet</h2>
          {action === "rename" && <label className="block"><span className="text-xs text-slate-500">New pallet number</span><input required maxLength={64} className="input" value={newNumber} onChange={(e) => setNewNumber(e.target.value)} /></label>}
          <label className="block"><span className="text-xs text-slate-500">Reason</span><textarea required maxLength={2000} rows={2} className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
          <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setAction(null)}>Cancel</button><button className={action === "archive" ? "btn-danger" : "btn-primary"} disabled={pending}>{pending ? "Applying…" : `Confirm ${action}`}</button></div>
        </form>
      )}
      {feedback && <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{feedback}</p>}

      <section className="card overflow-hidden">
        <header className="border-b p-4">
          <h2 className="font-semibold">Contained boxes</h2>
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <label className="relative"><span className="sr-only">Search boxes</span><Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-slate-400" /><input className="input pl-8" placeholder="Box number, File, item description, or barcode" value={search} onChange={(e) => { setSearch(e.target.value); setBoxPage(1); }} /></label>
            <select aria-label="Status" className="input" value={status} onChange={(e) => { setStatus(e.target.value as BoxStatus | ""); setBoxPage(1); }}><option value="">All statuses</option>{ALL_BOX_STATUSES.map((value) => <option key={value} value={value}>{STATUS_LABEL[value]}</option>)}</select>
            <select aria-label="Warehouse" className="input" value={boxWarehouseId} onChange={(e) => { setBoxWarehouseId(e.target.value ? Number(e.target.value) : ""); setBoxPage(1); }}><option value="">All accessible warehouses</option>{warehouses.data?.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}</select>
          </div>
        </header>
        <div className="overflow-x-auto">
          <table className="w-full text-sm"><thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500"><tr><th className="px-4 py-2.5">Box</th><th className="px-4 py-2.5">Status</th><th className="px-4 py-2.5">Warehouse</th><th className="px-4 py-2.5">Physical Files</th><th className="px-4 py-2.5">Updated</th></tr></thead><tbody className="divide-y">{boxes.data?.items.map((box) => <tr key={box.id}><td className="px-4 py-3"><Link className="font-mono text-brand-700 hover:underline" to={`/boxes/${box.id}`}>{box.box_number}</Link></td><td className="px-4 py-3"><StatusBadge status={box.status} /></td><td className="px-4 py-3">{warehouses.data?.find((warehouse) => warehouse.id === box.current_warehouse_id)?.name ?? `#${box.current_warehouse_id}`}</td><td className="px-4 py-3"><Link className="text-brand-700 hover:underline" to={`/files?box_id=${box.id}&activity=all`}>{box.active_file_count} active · {box.archived_file_count} archived</Link></td><td className="px-4 py-3 text-slate-500">{new Date(box.updated_at).toLocaleString()}</td></tr>)}</tbody></table>
          {!boxes.isLoading && boxes.data?.items.length === 0 && <p className="p-6 text-center text-sm text-slate-500">No contained boxes match.</p>}
        </div>
        {boxes.data && boxes.data.total > boxPageSize && (
          <footer className="flex items-center justify-between border-t px-4 py-3 text-sm">
            <span className="text-slate-500">Page {boxPage} of {boxPageCount} · {boxes.data.total} boxes</span>
            <div className="flex gap-2">
              <button className="btn-secondary" disabled={boxPage <= 1} onClick={() => setBoxPage((page) => Math.max(1, page - 1))}>Previous</button>
              <button className="btn-secondary" disabled={boxPage >= boxPageCount} onClick={() => setBoxPage((page) => Math.min(boxPageCount, page + 1))}>Next</button>
            </div>
          </footer>
        )}
      </section>

      {canWrite && summary.is_active && <PalletBoxControls pallet={summary} warehouses={warehouses.data ?? []} />}

      <section className="card card-pad">
        <h2 className="font-semibold">Pallet events</h2>
        {events.data?.length === 0 ? <p className="mt-2 text-sm text-slate-500">No pallet events yet.</p> : <ol className="mt-4 space-y-3">{events.data?.map((event) => <li key={event.id} className="border-l-2 border-brand-200 pl-3 text-sm"><p className="font-medium">{event.event_type.replaceAll("_", " ")}</p>{event.old_pallet_number && event.new_pallet_number && <p>{event.old_pallet_number} → {event.new_pallet_number}</p>}{event.reason && <p className="text-slate-600">Reason: {event.reason}</p>}<p className="text-xs text-slate-500">{new Date(event.occurred_at).toLocaleString()}</p></li>)}</ol>}
      </section>
    </div>
  );
}

function PalletBoxControls({ pallet, warehouses }: { pallet: import("@/api/types").PalletSummary; warehouses: Warehouse[] }) {
  const assign = useAssignPalletBoxes();
  const detach = useDetachPalletBoxes();
  const [mode, setMode] = useState<"assign" | "detach">("assign");
  const [candidatePage, setCandidatePage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [warehouseId, setWarehouseId] = useState<number | "">("");
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const candidatePageSize = 50;
  const candidates = useBoxes(
    palletCandidateFilters(
      pallet.lot_id,
      warehouseId || undefined,
      mode === "detach" ? pallet.id : undefined,
    ),
    candidatePage,
    candidatePageSize,
  );
  const candidatePageCount = Math.max(
    1,
    Math.ceil((candidates.data?.total ?? 0) / candidatePageSize),
  );
  const eligible = (candidates.data?.items ?? []).filter((box) => mode === "assign" ? box.pallet_id !== pallet.id : box.pallet_id === pallet.id);
  return (
    <section className="card card-pad">
      <h2 className="font-semibold">Assign or detach boxes</h2>
      <p className="mt-1 text-sm text-slate-600">Candidates can come from any accessible warehouse in this pallet’s lot. Assignment changes are audited and do not change each box’s status, warehouse, or active return reservations.</p>
      <div className="mt-3 flex gap-2"><button className={mode === "assign" ? "btn-primary" : "btn-secondary"} onClick={() => { setMode("assign"); setCandidatePage(1); setSelected(new Set()); }}>Assign / reassign</button><button className={mode === "detach" ? "btn-primary" : "btn-secondary"} onClick={() => { setMode("detach"); setCandidatePage(1); setSelected(new Set()); }}>Detach</button></div>
      <label className="mt-3 block max-w-sm"><span className="text-xs text-slate-500">Candidate warehouse (optional)</span><select className="input" value={warehouseId} onChange={(event) => { setWarehouseId(event.target.value ? Number(event.target.value) : ""); setCandidatePage(1); setSelected(new Set()); }}><option value="">All accessible warehouses</option>{warehouses.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}</select></label>
      <div className="mt-3 max-h-56 overflow-auto rounded border">{eligible.map((box) => <label key={box.id} className="flex items-center justify-between gap-3 border-b px-3 py-2 text-sm"><span><input className="mr-2" type="checkbox" checked={selected.has(box.id)} onChange={() => setSelected((current) => { const next = new Set(current); next.has(box.id) ? next.delete(box.id) : next.add(box.id); return next; })} />Box {box.box_number}</span><span className="text-right text-xs text-slate-500">{warehouses.find((warehouse) => warehouse.id === box.current_warehouse_id)?.name ?? `Warehouse #${box.current_warehouse_id}`} · {box.pallet_number ? `Currently ${box.pallet_number}` : "Unassigned"}</span></label>)}{eligible.length === 0 && <p className="p-3 text-sm text-slate-500">No eligible boxes.</p>}</div>
      {candidates.data && candidates.data.total > candidatePageSize && <div className="mt-2 flex items-center justify-between text-xs text-slate-500"><span>Candidate page {candidatePage} of {candidatePageCount}</span><div className="flex gap-2"><button className="btn-secondary" disabled={candidatePage <= 1} onClick={() => setCandidatePage((page) => Math.max(1, page - 1))}>Previous</button><button className="btn-secondary" disabled={candidatePage >= candidatePageCount} onClick={() => setCandidatePage((page) => Math.min(candidatePageCount, page + 1))}>Next</button></div></div>}
      <label className="mt-3 block"><span className="text-xs text-slate-500">Reason</span><input required className="input" maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <button className="btn-primary mt-3" disabled={!selected.size || !reason.trim() || assign.isPending || detach.isPending} onClick={async () => { try { const mutation = mode === "assign" ? assign : detach; const result = await mutation.mutateAsync({ id: pallet.id, payload: { box_ids: [...selected], reason: reason.trim() } }); setMessage(`${result.updated_box_ids.length} boxes updated${result.cancelled_request_ids.length ? `; cancelled request IDs ${result.cancelled_request_ids.join(", ")}` : ""}.`); setSelected(new Set()); setReason(""); } catch { setMessage("The box assignment could not be applied."); } }}>{mode === "assign" ? "Assign selected" : "Detach selected"}</button>
      {message && <p role="status" className="mt-2 text-sm text-amber-800">{message}</p>}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return <div className="rounded-lg bg-slate-50 p-3"><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 text-xl font-semibold">{value}</dd></div>;
}
