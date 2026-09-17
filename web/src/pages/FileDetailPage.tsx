import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Archive, ArrowLeft, MoveRight, Pencil, RotateCcw } from "lucide-react";
import {
  useArchiveFile,
  useBoxes,
  useFile,
  useFileEvents,
  useMoveFile,
  useRestoreFile,
  useUpdateFile,
  useWarehouses,
} from "@/api/hooks";
import type { TrackedFile } from "@/api/types";
import { useHasRole } from "@/components/RoleGate";
import { STATUS_LABEL, StatusBadge } from "@/components/StatusBadge";

type Action = "edit" | "move" | "archive" | "restore";

export function FileDetailPage() {
  const { id } = useParams<{ id: string }>();
  const fileId = id ? Number(id) : undefined;
  const isAdmin = useHasRole(["admin"]);
  const file = useFile(fileId, isAdmin, true);
  const events = useFileEvents(fileId);
  const canWrite = useHasRole(["admin", "operator"]);
  const [action, setAction] = useState<Action | null>(null);

  if (file.isLoading) return <p role="status" className="text-sm text-slate-500">Loading physical File…</p>;
  if (file.isError || !file.data) {
    return <div role="alert" className="card card-pad text-sm text-rose-700">This physical File could not be loaded. <Link className="underline" to="/files">Back to Files</Link></div>;
  }
  const item = file.data;

  return (
    <div className="space-y-6">
      <Link to="/files" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700">
        <ArrowLeft className="h-4 w-4" /> All physical Files
      </Link>

      <nav aria-label="File hierarchy" className="flex flex-wrap items-center gap-1 text-sm text-slate-600">
        <Link className="text-brand-700 hover:underline" to={`/lots/${item.lot_id}`}>{item.lot}</Link>
        <span aria-hidden="true">→</span>
        {item.pallet_id ? (
          <Link className="text-brand-700 hover:underline" to={`/pallets/${item.pallet_id}`}>{item.pallet ?? `Pallet #${item.pallet_id}`}</Link>
        ) : <span>Unassigned</span>}
        <span aria-hidden="true">→</span>
        <Link className="font-mono text-brand-700 hover:underline" to={`/boxes/${item.box_id}`}>Box {item.box}</Link>
        <span aria-hidden="true">→</span>
        <span aria-current="page">{item.reference}</span>
      </nav>

      <header className="card card-pad space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-mono text-2xl font-semibold">{item.reference}</h1>
              {!item.is_active && <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs">Archived</span>}
            </div>
            <p className="mt-1 text-sm text-slate-500">
              Physical File inventory record · version {item.version}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {canWrite && item.is_active && (
              <>
                <button className="btn-secondary" onClick={() => setAction("edit")}><Pencil className="h-4 w-4" /> Edit</button>
                <button className="btn-secondary" onClick={() => setAction("move")}><MoveRight className="h-4 w-4" /> Move</button>
                <button className="btn-danger" onClick={() => setAction("archive")}><Archive className="h-4 w-4" /> Archive</button>
              </>
            )}
            {isAdmin && item.archived_at !== null && (
              <button className="btn-primary" onClick={() => setAction("restore")}><RotateCcw className="h-4 w-4" /> Restore</button>
            )}
          </div>
        </div>
        <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Description">{item.description || "—"}</Field>
          <Field label="Barcode"><span className="font-mono">{item.barcode || "—"}</span></Field>
          <Field label="Inherited warehouse">{item.warehouse}</Field>
          <Field label="Inherited Box status"><StatusBadge status={item.status} /></Field>
          <Field label="Position in Box">{item.position}</Field>
          <Field label="Created">{new Date(item.created_at).toLocaleString()}</Field>
          <Field label="Updated">{new Date(item.updated_at).toLocaleString()}</Field>
          <Field label="Record ID">#{item.id}</Field>
        </dl>
        {!item.is_active && (
          <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            Archived {item.archived_at ? new Date(item.archived_at).toLocaleString() : ""}
            {item.archive_reason ? ` · ${item.archive_reason}` : ""}
          </p>
        )}
      </header>

      <section className="card card-pad">
        <h2 className="font-semibold">File audit timeline</h2>
        <p className="text-xs text-slate-500">Immutable changes to this physical File and its inherited Box context.</p>
        {events.isLoading && <p role="status" className="mt-4 text-sm text-slate-500">Loading timeline…</p>}
        {events.isError && <p role="alert" className="mt-4 text-sm text-rose-700">The timeline could not be loaded.</p>}
        {events.data?.length === 0 && <p className="mt-4 text-sm text-slate-500">No events yet.</p>}
        <ol className="mt-4 space-y-4">
          {events.data?.map((event) => (
            <li key={event.id} className="border-l-2 border-brand-200 pl-3 text-sm">
              <p className="font-medium capitalize">{event.event_type.replaceAll("_", " ")}</p>
              {event.reason && <p className="text-slate-600">Reason: {event.reason}</p>}
              <p className="text-xs text-slate-500">
                {new Date(event.occurred_at).toLocaleString()}
                {event.actor_user_id ? ` · User #${event.actor_user_id}` : ""}
              </p>
            </li>
          ))}
        </ol>
      </section>

      {action && <FileActionDialog file={item} action={action} onClose={() => setAction(null)} />}
    </div>
  );
}

function FileActionDialog({
  file,
  action,
  onClose,
}: {
  file: TrackedFile;
  action: Action;
  onClose: () => void;
}) {
  const update = useUpdateFile();
  const move = useMoveFile();
  const archive = useArchiveFile();
  const restore = useRestoreFile();
  const isAdmin = useHasRole(["admin"]);
  const destinations = useBoxes(
    { lot_id: file.lot_id, sort_by: "box_number", sort_dir: "asc" },
    1,
    200,
  );
  const warehouses = useWarehouses(true);
  const [reference, setReference] = useState(file.reference);
  const [description, setDescription] = useState(file.description ?? "");
  const [barcode, setBarcode] = useState(file.barcode ?? "");
  const [boxId, setBoxId] = useState<number | "">(file.box_id);
  const [reason, setReason] = useState("");
  const [force, setForce] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = update.isPending || move.isPending || archive.isPending || restore.isPending;
  const normalizedReference = reference.trim().replace(/\s+/g, " ").toLowerCase();
  const forceAllowed =
    isAdmin &&
    (action !== "edit" || normalizedReference !== file.reference.toLowerCase());
  const effectiveForce = forceAllowed && force;

  useEffect(() => {
    if (action === "move") setBoxId("");
  }, [action]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      if (action === "edit") {
        await update.mutateAsync({
          id: file.id,
          payload: {
            expected_version: file.version,
            reference: reference.trim(),
            description: description.trim() || null,
            barcode: barcode.trim() || null,
            force: effectiveForce,
            reason: effectiveForce ? reason.trim() || undefined : undefined,
          },
        });
      } else if (action === "move" && boxId) {
        await move.mutateAsync({
          id: file.id,
          payload: {
            box_id: Number(boxId),
            expected_version: file.version,
            force: effectiveForce,
            reason: reason.trim() || undefined,
          },
        });
      } else if (action === "archive") {
        await archive.mutateAsync({ id: file.id, payload: { expected_version: file.version, reason: reason.trim(), force: effectiveForce } });
      } else if (action === "restore") {
        await restore.mutateAsync({ id: file.id, payload: { expected_version: file.version, reason: reason.trim(), force: effectiveForce } });
      }
      onClose();
    } catch (caught) {
      const detail = (caught as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
      setError(typeof detail === "string" ? detail : "The File action could not be completed.");
    }
  }

  return (
    <div className="modal-backdrop z-40">
      <form
        className="modal-sheet max-h-[90vh] max-w-lg overflow-y-auto"
        role="dialog"
        aria-modal="true"
        aria-labelledby="file-action-title"
        onSubmit={submit}
      >
        <h2 id="file-action-title" className="text-lg font-semibold capitalize">{action} physical File</h2>
        {action === "edit" && (
          <div className="mt-4 space-y-3">
            <label className="block"><span className="text-xs text-slate-500">Reference</span><input required className="input" maxLength={255} value={reference} onChange={(event) => setReference(event.target.value)} /></label>
            <label className="block"><span className="text-xs text-slate-500">Description</span><textarea className="input" rows={3} maxLength={10000} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
            <label className="block"><span className="text-xs text-slate-500">Barcode</span><input className="input" maxLength={255} value={barcode} onChange={(event) => setBarcode(event.target.value)} /></label>
          </div>
        )}
        {action === "move" && (
          <div className="mt-4 space-y-3">
            <p className="text-sm text-slate-600">Only active destination Boxes in Lot {file.lot} are available. Their pallet and warehouse are shown before confirmation.</p>
            <label className="block"><span className="text-xs text-slate-500">Destination Box</span><select required className="input" value={boxId} onChange={(event) => setBoxId(event.target.value ? Number(event.target.value) : "")}><option value="">Choose active Box</option>{destinations.data?.items.filter((box) => box.id !== file.box_id && !box.archived_at).map((box) => <option key={box.id} value={box.id}>{box.box_number} · {box.pallet_number ?? "Unassigned"} · {warehouses.data?.find((warehouse) => warehouse.id === box.current_warehouse_id)?.name ?? `warehouse #${box.current_warehouse_id}`} · {STATUS_LABEL[box.status]}</option>)}</select></label>
          </div>
        )}
        {action !== "edit" && (
          <label className="mt-4 block">
            <span className="text-xs text-slate-500">Reason</span>
            <textarea required={action !== "move"} className="input" rows={3} maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Recorded in the File audit timeline" />
          </label>
        )}
        {action === "edit" && effectiveForce && (
          <label className="mt-4 block">
            <span className="text-xs text-slate-500">Override reason</span>
            <textarea required className="input" rows={3} maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
        )}
        {forceAllowed && (
          <label className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
            <input type="checkbox" className="mt-1" checked={force} onChange={(event) => setForce(event.target.checked)} />
            <span>Force this action and cancel any active return reservation. A reason is required and audited.</span>
          </label>
        )}
        {(action === "archive" || action === "restore") && (
          <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            Confirm this changes only the physical File record. The containing Box remains in its current workflow.
          </p>
        )}
        {error && <p role="alert" className="mt-3 text-sm text-rose-600">{error}</p>}
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={pending}>Cancel</button>
          <button className={action === "archive" ? "btn-danger" : "btn-primary"} disabled={pending || (action === "move" && !boxId) || (action !== "edit" && action !== "move" && !reason.trim()) || (effectiveForce && !reason.trim())}>
            {pending ? "Applying…" : `Confirm ${action}`}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><dt className="text-xs uppercase tracking-wider text-slate-400">{label}</dt><dd className="mt-1 break-words text-slate-700">{children}</dd></div>;
}
