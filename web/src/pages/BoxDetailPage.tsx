import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Trash2 } from "lucide-react";
import {
  useBox,
  useBoxEvents,
  useDeleteBox,
  useLot,
  useReassignBoxLot,
  useUpdateBox,
  useWarehouses,
} from "@/api/hooks";
import { ALL_BOX_STATUSES } from "@/api/types";
import type { BoxStatus } from "@/api/types";
import { STATUS_LABEL, StatusBadge } from "@/components/StatusBadge";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { LotPicker, type LotSelection } from "@/components/LotPicker";
import { useHasRole } from "@/components/RoleGate";
import {
  formatCancelledRequestIds,
  hasRequiredOverrideReason,
  shouldOfferForceArchive,
} from "@/pages/boxIntegrity";
import { reassignmentPayload } from "@/pages/lots";

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
  const sourceLot = useLot(box?.lot_id);
  const warehouses = useWarehouses(true);
  const update = useUpdateBox();
  const deleteBox = useDeleteBox();
  const reassignLot = useReassignBoxLot();
  const canWrite = useHasRole(["admin", "operator"]);
  const isAdmin = useHasRole(["admin"]);
  const [override, setOverride] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteConflict, setDeleteConflict] = useState<string | null>(null);
  const [archiveReason, setArchiveReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [targetLot, setTargetLot] = useState<LotSelection | null>(null);
  const [reassignmentReason, setReassignmentReason] = useState("");
  const [reassignmentError, setReassignmentError] = useState<string | null>(null);
  const [relocationWarehouseId, setRelocationWarehouseId] = useState<number | "">(
    "",
  );
  const [relocationReason, setRelocationReason] = useState("");
  const [relocationCancelledIds, setRelocationCancelledIds] = useState<
    number[] | null
  >(null);
  const [relocationError, setRelocationError] = useState<string | null>(null);

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
    box.status === "returned";
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
            <p className="text-sm text-slate-500">
              Lot:{" "}
              <Link className="text-brand-700 hover:underline" to={`/lots/${box.lot_id}`}>
                {box.lot}
              </Link>
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
              title="Allow protected status changes. Audit history will tag the change as an admin override."
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
            {box.status !== "returned" && (
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
            )}
          </div>
        )}
        {actionError && (
          <p role="alert" className="text-sm text-rose-600">
            {actionError}
          </p>
        )}
      </header>

      {isAdmin && box.status === "returned" && !box.archived_at && (
        <section className="card card-pad border-amber-200 bg-amber-50/40">
          <h2 className="font-semibold">Relocate returned box</h2>
          <p className="mt-1 text-sm text-slate-600">
            Admin-only audited correction. The box remains returned and keeps
            its returned timestamp. Conflicting active return reservations will
            be cancelled.
          </p>
          <form
            className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!relocationWarehouseId || !relocationReason.trim()) return;
              setRelocationError(null);
              setRelocationCancelledIds(null);
              try {
                const result = await update.mutateAsync({
                  id: box.id,
                  patch: {
                    warehouse_id: relocationWarehouseId,
                    force: true,
                    note: relocationReason.trim(),
                  },
                });
                setRelocationCancelledIds(result.cancelled_request_ids);
                setRelocationWarehouseId("");
                setRelocationReason("");
              } catch (caught) {
                setRelocationError(
                  apiError(caught, "The returned box could not be relocated."),
                );
              }
            }}
          >
            <label className="block">
              <span className="text-xs text-slate-500">
                Active destination warehouse
              </span>
              <select
                required
                className="input"
                value={relocationWarehouseId}
                disabled={update.isPending}
                onChange={(event) =>
                  setRelocationWarehouseId(
                    event.target.value ? Number(event.target.value) : "",
                  )
                }
              >
                <option value="">Choose destination</option>
                {warehouses.data
                  ?.filter(
                    (warehouse) =>
                      warehouse.is_active &&
                      warehouse.id !== box.current_warehouse_id,
                  )
                  .map((warehouse) => (
                    <option key={warehouse.id} value={warehouse.id}>
                      {warehouse.name}
                    </option>
                  ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-slate-500">Relocation reason</span>
              <input
                required
                className="input"
                maxLength={2000}
                value={relocationReason}
                disabled={update.isPending}
                onChange={(event) => setRelocationReason(event.target.value)}
                placeholder="Required for audit history"
              />
            </label>
            <button
              type="submit"
              className="btn-primary"
              disabled={
                update.isPending ||
                !relocationWarehouseId ||
                !relocationReason.trim()
              }
            >
              {update.isPending ? "Relocating…" : "Relocate returned box"}
            </button>
          </form>
          {relocationCancelledIds !== null && (
            <p className="mt-3 text-sm text-amber-900">
              {relocationCancelledIds.length > 0
                ? `Cancelled conflicting return request IDs: ${formatCancelledRequestIds(
                    relocationCancelledIds,
                  )}.`
                : "No conflicting active return reservations were cancelled."}
            </p>
          )}
          {relocationError && (
            <p role="alert" className="mt-3 text-sm text-rose-600">
              {relocationError}
            </p>
          )}
        </section>
      )}

      {isAdmin && !box.archived_at && (
        <section className="card card-pad">
          <h2 className="font-semibold">Reassign lot</h2>
          <p className="mt-1 text-sm text-slate-600">
            Admin-only audited correction. This moves the existing box to a
            different lot and requires a reason.
          </p>
          <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end">
            <LotPicker
              label="Target lot"
              value={targetLot}
              onChange={setTargetLot}
            />
            <label className="block">
              <span className="text-xs text-slate-500">Correction reason</span>
              <input
                className="input"
                maxLength={2000}
                value={reassignmentReason}
                onChange={(event) => setReassignmentReason(event.target.value)}
                placeholder="Required for audit history"
              />
            </label>
            <button
              type="button"
              className="btn-primary"
              disabled={
                reassignLot.isPending ||
                !targetLot ||
                targetLot.id === box.lot_id ||
                !reassignmentReason.trim() ||
                !sourceLot.data
              }
              onClick={async () => {
                if (!targetLot || !sourceLot.data) return;
                setReassignmentError(null);
                try {
                  await reassignLot.mutateAsync({
                    boxId: box.id,
                    sourceLotId: box.lot_id,
                    payload: reassignmentPayload(
                      targetLot.id,
                      reassignmentReason,
                      sourceLot.data.version,
                    ),
                  });
                  setTargetLot(null);
                  setReassignmentReason("");
                } catch (caught) {
                  const status = (caught as { response?: { status?: number } })
                    .response?.status;
                  if (status === 409) {
                    await sourceLot.refetch();
                    setReassignmentError(
                      "The source lot changed while you were editing. Latest state loaded; review and retry.",
                    );
                  } else {
                    setReassignmentError(
                      apiError(caught, "The box could not be reassigned."),
                    );
                  }
                }
              }}
            >
              {reassignLot.isPending ? "Reassigning…" : "Reassign box"}
            </button>
          </div>
          {reassignmentError && (
            <p role="alert" className="mt-3 text-sm text-rose-600">
              {reassignmentError}
            </p>
          )}
        </section>
      )}

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
    case "lot_reassigned":
      return "Lot reassigned by an administrator.";
    default:
      return ev.event_type;
  }
}

function apiError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    .response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}
