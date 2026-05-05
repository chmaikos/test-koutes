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

const NEXT_STATUS: Record<BoxStatus, BoxStatus[]> = {
  received: ["ready_to_return"],
  ready_to_return: ["returned"],
  returned: [],
};

export function BoxDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const boxId = id ? Number(id) : undefined;
  const { data: box } = useBox(boxId);
  const events = useBoxEvents(boxId);
  const warehouses = useWarehouses();
  const update = useUpdateBox();
  const deleteBox = useDeleteBox();
  const canWrite = useHasRole(["admin", "operator"]);
  const isAdmin = useHasRole(["admin"]);
  const [override, setOverride] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

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
    update.isPending || (!useOverride && box.status === "returned");

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
            <p className="text-sm text-slate-500">
              {box.owner ? `Owner: ${box.owner}` : "Owner unspecified"}
            </p>
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
        </dl>

        {isAdmin && (
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
              <span className="text-xs text-amber-700">
                Constrained transitions disabled. Audit log will record this as
                an admin override.
              </span>
            )}
          </div>
        )}

        {canWrite && (statusButtons.length > 0 || useOverride) && (
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            <span className="text-xs text-slate-500">Move to:</span>
            {statusButtons.map((s) => (
              <button
                type="button"
                key={s}
                className="btn-secondary text-xs"
                disabled={update.isPending}
                onClick={() =>
                  update.mutate({
                    id: box.id,
                    patch: { status: s, force: useOverride || undefined },
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
                update.mutate({
                  id: box.id,
                  patch: {
                    warehouse_id: Number(e.target.value),
                    force: useOverride || undefined,
                  },
                })
              }
            >
              {warehouses.data?.map((w) => (
                <option key={w.id} value={w.id}>
                  Move to {w.name}
                </option>
              ))}
            </select>
          </div>
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

      {isAdmin && (
        <section className="card card-pad border-rose-200 bg-rose-50/50">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-rose-700">
            Danger zone
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            Permanently delete this box and its event history. This action
            cannot be undone.
          </p>
          <div className="mt-3">
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-md border border-rose-200 bg-white px-3 py-1.5 text-sm font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-50"
              onClick={() => setConfirmDelete(true)}
              disabled={deleteBox.isPending}
            >
              <Trash2 className="h-4 w-4" /> Delete box
            </button>
          </div>
        </section>
      )}

      {confirmDelete && (
        <ConfirmDialog
          title={`Delete box ${box.box_number}?`}
          message={
            <>
              The box (currently <strong>{STATUS_LABEL[box.status]}</strong> at{" "}
              <strong>{warehouseName}</strong>) and its full event history will
              be removed. This cannot be undone.
            </>
          }
          confirmLabel="Delete"
          destructive
          isPending={deleteBox.isPending}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={async () => {
            await deleteBox.mutateAsync(box.id);
            setConfirmDelete(false);
            navigate("/boxes", { replace: true });
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
    default:
      return ev.event_type;
  }
}
