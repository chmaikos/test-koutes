import { useState } from "react";
import { Link } from "react-router-dom";
import { useMergeLots, useRenameLot } from "@/api/hooks";
import type { LotMergeCandidate, LotSummary } from "@/api/types";
import {
  lotConflictCurrent,
  lotMergeCandidate,
  lotMergeCandidateClassification,
  lotMergeConfirmationIsValid,
  lotMergeConflictCode,
  lotMergeConflictFormState,
  lotMergeConflictNeedsRefresh,
  lotMergePayload,
  lotRenamePayload,
  renameValidation,
} from "@/pages/lots";

export function RenameLotDialog({
  lot,
  onClose,
  onMerged,
}: {
  lot: LotSummary;
  onClose: () => void;
  onMerged: (targetLotId: number) => void;
}) {
  const rename = useRenameLot();
  const merge = useMergeLots();
  const [name, setName] = useState(lot.name);
  const [reason, setReason] = useState("");
  const [version, setVersion] = useState(lot.version);
  const [error, setError] = useState<string | null>(null);
  const [latestMessage, setLatestMessage] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<LotMergeCandidate | null>(null);
  const [overwriteAcknowledged, setOverwriteAcknowledged] = useState(false);
  const mergeClassification = candidate
    ? lotMergeCandidateClassification(candidate)
    : null;

  return (
    <div className="modal-backdrop z-40">
      <form
        className="modal-sheet max-w-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rename-lot-title"
        onSubmit={async (event) => {
          event.preventDefault();
          const validation = renameValidation(name, reason);
          if (validation) {
            setError(validation);
            return;
          }
          setError(null);
          setLatestMessage(null);
          setCandidate(null);
          try {
            await rename.mutateAsync({
              id: lot.id,
              payload: lotRenamePayload(name, reason, version),
            });
            onClose();
          } catch (caught) {
            const mergeCandidate = lotMergeCandidate(caught);
            if (mergeCandidate) {
              setVersion(mergeCandidate.source.version);
              setCandidate(mergeCandidate);
              setOverwriteAcknowledged(false);
              return;
            }
            const latest = lotConflictCurrent(caught);
            if (latest) {
              setName(latest.name);
              setVersion(latest.version);
              setLatestMessage(
                `Another admin changed this lot to “${latest.name}”. The latest state is loaded; review it and retry with your correction reason.`,
              );
            } else {
              const detail = (
                caught as { response?: { data?: { detail?: unknown } } }
              ).response?.data?.detail;
              setError(
                typeof detail === "string"
                  ? detail
                  : "The lot could not be renamed.",
              );
            }
          }
        }}
      >
        <h2 id="rename-lot-title" className="text-lg font-semibold">
          Correct lot name
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          This audited correction updates the name everywhere. It does not
          merge lots unless you explicitly confirm a collision below.
        </p>
        <label className="mt-4 block">
          <span className="text-xs font-medium text-slate-600">New name</span>
          <input
            autoFocus
            required
            maxLength={64}
            className="input mt-1"
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              setCandidate(null);
              setOverwriteAcknowledged(false);
            }}
          />
        </label>
        <label className="mt-3 block">
          <span className="text-xs font-medium text-slate-600">
            Correction reason
          </span>
          <textarea
            required
            maxLength={2000}
            rows={3}
            className="input mt-1"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Required for the audit history"
          />
        </label>
        {latestMessage && (
          <p role="status" className="mt-3 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
            {latestMessage}
          </p>
        )}
        {candidate && (
          <section
            aria-labelledby="merge-candidate-title"
            className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
          >
            <h3 id="merge-candidate-title" className="font-semibold">
              Lot “{candidate.target.name}” already exists.
            </h3>
            {candidate.pallet_actions.length > 0 && (
              <div className="mt-3 rounded border border-sky-200 bg-sky-50 p-3 text-sky-950">
                <h4 className="font-semibold">Pallet actions</h4>
                <ul className="mt-1 space-y-1">
                  {candidate.pallet_actions.map((action) => (
                    <li key={action.source_pallet_id}>
                      Pallet{" "}
                      <Link className="text-brand-700 underline" to={`/pallets/${action.source_pallet_id}`}>
                        {action.source_pallet_number}
                      </Link>{" "}
                      will {action.action === "combine" ? `combine into pallet #${action.target_pallet_id}` : "transfer to the target lot"} ({action.box_count} boxes).
                    </li>
                  ))}
                </ul>
                {candidate.pallet_actions_truncated && <p className="mt-1">Showing {candidate.pallet_actions.length} of {candidate.pallet_action_count} actions.</p>}
              </div>
            )}
            {candidate.pallet_collisions.length > 0 && (
              <div className="mt-3 rounded border border-rose-300 bg-rose-50 p-3 text-rose-950">
                <h4 className="font-semibold">Pallet collisions block this merge</h4>
                <ul className="mt-1 space-y-1">
                  {candidate.pallet_collisions.map((collision) => (
                    <li key={`${collision.source_pallet_id}:${collision.target_pallet_id}`}>
                      “{collision.source_pallet_number}” conflicts with “{collision.target_pallet_number}”:{" "}
                      {collision.reason === "warehouse_mismatch"
                        ? "same normalized number in different warehouses"
                        : "the target pallet is archived"}.
                    </li>
                  ))}
                </ul>
                <p className="mt-2">Move, rename, restore, or otherwise reconcile these pallets, then retry to load a fresh merge preview.</p>
              </div>
            )}
            {mergeClassification === "normal" && (
              <p className="mt-1">
                You may merge “{candidate.source.name}” into it. Every current
                and archived box and current lot link will move to the target;
                historical request and XLSX text remains unchanged. This action
                is explicit and audited.
              </p>
            )}
            {mergeClassification === "resolvable" && (
              <>
                <p className="mt-1 font-semibold text-rose-800">
                  This merge can continue only by permanently overwriting{" "}
                  {candidate.resolvable_archived_collision_count} archived{" "}
                  {candidate.resolvable_archived_collision_count === 1
                    ? "box"
                    : "boxes"}
                  .
                </p>
                <p className="mt-1">
                  The active box wins. When both boxes are archived, the target
                  lot&apos;s box is kept. Each removed archived box and its box
                  events are permanently deleted. Request-item and discrepancy
                  history is relinked to the survivor; immutable historical lot
                  and box text remains unchanged.
                </p>
                <ul className="mt-3 space-y-2">
                  {candidate.resolvable_archived_collisions.map((collision) => {
                    const survivorArchived =
                      collision.survivor_lot_side === "source"
                        ? collision.source_box_archived
                        : collision.target_box_archived;
                    const survivorLot =
                      collision.survivor_lot_side === "source"
                        ? candidate.source
                        : candidate.target;
                    const removedLot =
                      collision.removed_lot_side === "source"
                        ? candidate.source
                        : candidate.target;
                    return (
                      <li
                        key={`${collision.box_number}-${collision.removed_box_id}`}
                        className="rounded border border-amber-300 bg-white/70 p-2"
                      >
                        <span className="font-semibold">
                          Box number {collision.box_number}
                        </span>
                        <span className="mt-1 block">
                          Remove archived box{" "}
                          <Link
                            className="font-medium text-brand-700 underline"
                            to={`/boxes/${collision.removed_box_id}`}
                          >
                            #{collision.removed_box_id}
                          </Link>{" "}
                          from {removedLot.name} ({collision.removed_lot_side});
                          keep{" "}
                          <Link
                            className="font-medium text-brand-700 underline"
                            to={`/boxes/${collision.survivor_box_id}`}
                          >
                            box #{collision.survivor_box_id}
                          </Link>{" "}
                          from {survivorLot.name} (
                          {collision.survivor_lot_side},{" "}
                          {survivorArchived ? "archived" : "active"}).
                        </span>
                        <span className="mt-1 block text-slate-700">
                          Relink {collision.request_item_relink_count} request{" "}
                          {collision.request_item_relink_count === 1
                            ? "item"
                            : "items"}{" "}
                          and {collision.discrepancy_relink_count}{" "}
                          {collision.discrepancy_relink_count === 1
                            ? "discrepancy"
                            : "discrepancies"}
                          ; permanently remove {collision.box_event_delete_count}{" "}
                          box{" "}
                          {collision.box_event_delete_count === 1
                            ? "event"
                            : "events"}
                          .
                        </span>
                      </li>
                    );
                  })}
                </ul>
                {candidate.resolvable_archived_collisions_truncated && (
                  <p className="mt-2 font-medium text-rose-800">
                    Showing{" "}
                    {candidate.resolvable_archived_collisions.length} of{" "}
                    {candidate.resolvable_archived_collision_count} archived
                    overwrites. All will be applied.
                  </p>
                )}
                <label
                  className="mt-3 flex items-start gap-2 rounded border border-rose-300 bg-rose-50 p-3 text-rose-950"
                  htmlFor="acknowledge-archived-overwrite"
                >
                  <input
                    id="acknowledge-archived-overwrite"
                    type="checkbox"
                    className="mt-0.5"
                    checked={overwriteAcknowledged}
                    disabled={merge.isPending}
                    onChange={(event) =>
                      setOverwriteAcknowledged(event.target.checked)
                    }
                  />
                  <span>
                    I understand that all{" "}
                    {candidate.resolvable_archived_collision_count} archived
                    collision boxes and their box events will be permanently
                    removed, including any entries omitted from the bounded
                    list above, while linked request and discrepancy history
                    will be preserved on the survivor.
                  </span>
                </label>
              </>
            )}
            {mergeClassification === "hard" && (
              <>
                <p className="mt-1 font-semibold text-rose-800">
                  Merge is blocked by active-active box number overlaps:
                </p>
                <ul className="mt-2 space-y-1 text-rose-800">
                  {candidate.hard_overlaps.map((overlap) => (
                    <li key={overlap.box_number}>
                      <span className="font-mono font-semibold">
                        {overlap.box_number}
                      </span>{" "}
                      — source active box IDs{" "}
                      {overlap.source_active_box_ids.join(", ") || "none"};
                      target active box IDs{" "}
                      {overlap.target_active_box_ids.join(", ") || "none"}
                    </li>
                  ))}
                </ul>
                {candidate.hard_overlaps_truncated && (
                  <p className="mt-1 text-rose-800">
                    Showing {candidate.hard_overlaps.length} of{" "}
                    {candidate.hard_overlap_count} hard overlaps.
                  </p>
                )}
                <p className="mt-2">
                  Reassign, renumber, or archive one physical box for every
                  active-active number, then retry the rename and review a new
                  merge preview.
                </p>
              </>
            )}
          </section>
        )}
        {error && (
          <p role="alert" className="mt-3 text-sm text-rose-600">
            {error}
          </p>
        )}
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            className="btn-secondary"
            disabled={rename.isPending || merge.isPending}
            onClick={onClose}
          >
            Cancel
          </button>
          {candidate && mergeClassification !== "hard" ? (
            <button
              type="button"
              className="btn-danger"
              disabled={
                merge.isPending ||
                !lotMergeConfirmationIsValid(
                  candidate,
                  reason,
                  overwriteAcknowledged,
                )
              }
              onClick={async () => {
                if (
                  !lotMergeConfirmationIsValid(
                    candidate,
                    reason,
                    overwriteAcknowledged,
                  )
                ) {
                  return;
                }
                setError(null);
                setLatestMessage(null);
                try {
                  const result = await merge.mutateAsync({
                    sourceId: lot.id,
                    payload: lotMergePayload(
                      candidate,
                      reason,
                      mergeClassification === "resolvable",
                    ),
                  });
                  onClose();
                  onMerged(result.target.id);
                } catch (caught) {
                  const refreshed = lotMergeCandidate(caught);
                  const code = lotMergeConflictCode(caught);
                  if (refreshed && lotMergeConflictNeedsRefresh(code)) {
                    const reset = lotMergeConflictFormState(reason);
                    setCandidate(refreshed);
                    setVersion(refreshed.source.version);
                    setReason(reset.reason);
                    setOverwriteAcknowledged(reset.overwriteAcknowledged);
                    setLatestMessage(
                      "The source, target, overlap, or linked inventory graph changed. The latest merge check is loaded; review it and explicitly confirm again.",
                    );
                    return;
                  }
                  setCandidate(null);
                  setOverwriteAcknowledged(false);
                  setError(
                    code === "already_merged"
                      ? "One of these lots has already been merged. Reload the lot details."
                      : "The lots could not be merged. Reload and try again.",
                  );
                }
              }}
            >
              {merge.isPending
                ? "Merging…"
                : mergeClassification === "resolvable"
                  ? `Merge and overwrite ${candidate.resolvable_archived_collision_count} archived ${
                      candidate.resolvable_archived_collision_count === 1
                        ? "box"
                        : "boxes"
                    }`
                  : `Merge into “${candidate.target.name}”`}
            </button>
          ) : !candidate ? (
            <button
              type="submit"
              className="btn-primary"
              disabled={rename.isPending || !!renameValidation(name, reason)}
            >
              {rename.isPending ? "Saving…" : "Confirm correction"}
            </button>
          ) : null}
        </div>
      </form>
    </div>
  );
}

