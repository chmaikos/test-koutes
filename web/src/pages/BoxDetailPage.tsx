import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Trash2 } from "lucide-react";
import {
  useBox,
  useBoxEvents,
  useDeleteBox,
  useUpdateBox,
  useWarehouses,
} from "@/api/hooks";
import { ALL_BOX_STATUSES } from "@/api/types";
import type { BoxStatus } from "@/api/types";
import { STATUS_LABEL, StatusBadge } from "@/components/StatusBadge";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useHasRole } from "@/components/RoleGate";
import {
  hasRequiredOverrideReason,
  shouldOfferForceArchive,
} from "@/pages/boxIntegrity";

// Mirrors the API's linear chain. Each entry is "the next legal step"
// for that state; admins toggling "Override rules" below get the full
// status list instead of just this single button.
const NEXT_STATUS: Record<BoxStatus, BoxStatus[]> = {
  quarantined: [],
  received: ["processing"],
  processing: ["incomplete"],
  incomplete: ["ready_to_return"],
  ready_to_return: ["returned"],
  returned: [],
};

export function BoxDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const boxId = id ? Number(id) : undefined;
  const { data: box } = useBox(boxId);
  const events = useBoxEvents(boxId);
  const warehouses = useWarehouses(true);
  const update = useUpdateBox();
  const deleteBox = useDeleteBox();
  const canWrite = useHasRole(["admin", "operator"]);
  const isAdmin = useHasRole(["admin"]);
  const [override, setOverride] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteConflict, setDeleteConflict] = useState<string | null>(null);
  const [archiveReason, setArchiveReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  if (!box) {
    return <p className="text-sm text-slate-500">Loading...</p>;
  }

  const warehouseName =
    warehouses.data?.find((w) => w.id === box.current_warehouse_id)?.name ??
    `#${box.current_warehouse_id}`;
  const useOverride = isAdmin && override;
  const transitions = NEXT_STATUS[box.status] ?? [];
  const statusButtons: BoxStatus[] = useOverride
    ? ALL_BOX_STATUSES.filter((s) => s !== box.status)
    : transitions;
  const warehouseSelectDisabled =
    update.isPending ||
    box.archived_at !== null ||
    !hasRequiredOverrideReason(useOverride, overrideReason) ||
    (!useOverride && box.status === "returned");
  const currentBoxId = box.id;

  async function performUpdate(
    patch: Parameters<typeof update.mutateAsync>[0]["patch"],
  ) {
    setActionError(null);
    try {
      await update.mutateAsync({ id: currentBoxId, patch });
    } catch (caught) {
      setActionError(apiError(caught, "The box could not be updated."));
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/boxes"
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
        >
          <ArrowLeft className="h-4 w-4" /> All boxes
        </Link>
      </div>

      <header className="card card-pad space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-mono text-2xl font-semibold">{box.box_number}</h1>
            <p className="text-sm text-slate-500">Lot: {box.lot}</p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <StatusBadge status={box.status} />
            <span className="text-xs text-slate-500">at {warehouseName}</span>
          </div>
        </div>
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <Field label="Received">
            {box.received_at ? new Date(box.received_at).toLocaleString() : "—"}
          </Field>
          <Field label="Returned">
            {box.returned_at ? new Date(box.returned_at).toLocaleString() : "—"}
          </Field>
          <Field label="Contents">
            {box.contents ?? "—"}
          </Field>
        </dl>
        {box.archived_at && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <strong>Archived:</strong>{" "}
            {new Date(box.archived_at).toLocaleString()}
            {box.archive_reason ? ` — ${box.archive_reason}` : ""}
          </div>
        )}

        {isAdmin && !box.archived_at && (
          <div className="flex items-center justify-between border-t border-slate-100 pt-3">
            <label
              className="flex items-center gap-1.5 text-xs text-amber-800"
              title="Allow any status change or moving a returned box. Audit log will tag the change as [admin override]."
            >
              <input
                type="checkbox"
                checked={override}
                onChange={(e) => setOverride(e.target.checked)}
                disabled={update.isPending}
              />
              Override rules (admin)
            </label>
            {useOverride && (
              <input
                className="input max-w-sm text-xs"
                placeholder="Required override reason"
                maxLength={2000}
                value={overrideReason}
                onChange={(event) => setOverrideReason(event.target.value)}
              />
            )}
          </div>
        )}

        {canWrite &&
          !box.archived_at &&
          (statusButtons.length > 0 || useOverride) && (
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            <span className="text-xs text-slate-500">Move to:</span>
            {statusButtons.map((s) => (
              <button
                type="button"
                key={s}
                className="btn-secondary text-xs"
                disabled={
                  update.isPending ||
                  !hasRequiredOverrideReason(useOverride, overrideReason)
                }
                onClick={() =>
                  void performUpdate({
                    status: s,
                    force: useOverride || undefined,
                    note: useOverride ? overrideReason.trim() : undefined,
                  })
                }
              >
                {STATUS_LABEL[s]}
              </button>
            ))}
            <select
              className="input ml-auto inline-block w-auto text-xs"
              value={box.current_warehouse_id}
              disabled={warehouseSelectDisabled}
              onChange={(e) =>
                void performUpdate({
                  warehouse_id: Number(e.target.value),
                  force: useOverride || undefined,
                  note: useOverride ? overrideReason.trim() : undefined,
                })
              }
            >
              {warehouses.data?.filter((w) => w.is_active).map((w) => (
                <option key={w.id} value={w.id}>
                  Move to {w.name}
                </option>
              ))}
            </select>
          </div>
        )}
        {actionError && (
          <p role="alert" className="text-sm text-rose-600">
            {actionError}
          </p>
        )}
      </header>

      <section className="card card-pad">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500">
          Activity
        </h2>
        <ol className="mt-3 space-y-3">
          {events.data?.length === 0 && (
            <li className="text-sm text-slate-400">No events yet.</li>
          )}
          {events.data?.map((ev) => (
            <li key={ev.id} className="flex gap-3">
              <div className="mt-1 h-2.5 w-2.5 flex-none rounded-full bg-brand-500" />
              <div>
                <div className="text-sm">
                  {describeEvent(ev, warehouses.data ?? [])}
                </div>
                <div className="text-xs text-slate-500">
                  {new Date(ev.occurred_at).toLocaleString()}
                </div>
                {ev.note && (
                  <div className="mt-1 text-xs italic text-slate-500">
                    {ev.note}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      </section>

      {isAdmin && !box.archived_at && (
        <section className="card card-pad border-rose-200 bg-rose-50/50">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-rose-700">
            Danger zone
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            Unlinked boxes can be permanently deleted. A box referenced by a
            request is protected; an admin may archive it with a reason instead.
          </p>
          <div className="mt-3">
            <button
              type="button"
              className="btn-secondary border-rose-200 !ring-rose-200 text-rose-700 hover:bg-rose-100"
              onClick={() => setConfirmDelete(true)}
              disabled={deleteBox.isPending}
            >
              <Trash2 className="h-4 w-4" /> Delete box
            </button>
          </div>
          {deleteConflict && (
            <div className="mt-4 rounded-lg border border-amber-200 bg-white p-3">
              <p className="text-sm text-amber-900">{deleteConflict}</p>
              <label className="mt-3 block">
                <span className="text-xs font-medium text-slate-600">
                  Archive reason
                </span>
                <textarea
                  className="input mt-1"
                  rows={3}
                  maxLength={2000}
                  value={archiveReason}
                  onChange={(event) => setArchiveReason(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="btn-danger mt-3"
                disabled={
                  deleteBox.isPending ||
                  !hasRequiredOverrideReason(true, archiveReason)
                }
                onClick={async () => {
                  try {
                    await deleteBox.mutateAsync({
                      id: box.id,
                      force: true,
                      reason: archiveReason.trim(),
                    });
                    navigate("/boxes", { replace: true });
                  } catch (caught) {
                    setDeleteConflict(
                      apiError(caught, "The box could not be archived."),
                    );
                  }
                }}
              >
                Archive linked box
              </button>
            </div>
          )}
        </section>
      )}

      {confirmDelete && (
        <ConfirmDialog
          title={`Delete box ${box.box_number}?`}
          message={
            <>
              If this box is not referenced by a request, it and its event
              history will be removed permanently. Request-linked boxes are
              protected and can be archived after this check.
            </>
          }
          confirmLabel="Delete"
          destructive
          isPending={deleteBox.isPending}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={async () => {
            try {
              await deleteBox.mutateAsync({ id: box.id });
              setConfirmDelete(false);
              navigate("/boxes", { replace: true });
            } catch (caught) {
              setConfirmDelete(false);
              const message = apiError(caught, "The box could not be deleted.");
              const responseStatus = (
                caught as { response?: { status?: number } }
              ).response?.status;
              if (shouldOfferForceArchive(responseStatus)) {
                setDeleteConflict(message);
              } else {
                setActionError(message);
              }
            }
          }}
        />
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wider text-slate-400">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function describeEvent(
  ev: import("@/api/types").BoxEvent,
  warehouses: import("@/api/types").Warehouse[],
): string {
  const wname = (id: number | null) =>
    id ? (warehouses.find((w) => w.id === id)?.name ?? `#${id}`) : "?";
  switch (ev.event_type) {
    case "created":
      return `Received at ${wname(ev.to_warehouse_id)}.`;
    case "moved":
      return `Moved from ${wname(ev.from_warehouse_id)} to ${wname(ev.to_warehouse_id)}.`;
    case "status_changed":
      return `Status: ${ev.from_status ? STATUS_LABEL[ev.from_status] : "?"} → ${
        ev.to_status ? STATUS_LABEL[ev.to_status] : "?"
      }.`;
    case "returned":
      return `Returned from ${wname(ev.warehouse_id)}.`;
    case "archived":
      return `Archived at ${wname(ev.warehouse_id)}.`;
    case "restored":
      return `Restored to active inventory at ${wname(ev.to_warehouse_id)}.`;
    default:
      return ev.event_type;
  }
}

function apiError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    .response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}
