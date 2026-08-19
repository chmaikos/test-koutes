import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, RefreshCw, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import {
  queryKeys,
  useForcePurgeLot,
  useLotForcePurgePreview,
} from "@/api/hooks";
import type {
  LotForcePurgeBlocker,
  LotForcePurgeResult,
} from "@/api/types";
import {
  FORCE_PURGE_MIN_REASON_LENGTH,
  forcePurgeChange,
  forcePurgeConfirmationIsValid,
  forcePurgeConflictFormState,
  forcePurgeHasHardBlock,
  forcePurgeSiblingPreservationText,
  lotForcePurgeConflict,
  lotForcePurgePayload,
} from "@/pages/lots";

export function ForcePurgeEscalationPanel({
  lotId,
  onSuccess,
}: {
  lotId: number;
  onSuccess: (result: LotForcePurgeResult) => void;
}) {
  const queryClient = useQueryClient();
  const preview = useLotForcePurgePreview(lotId, true);
  const execute = useForcePurgeLot();
  const [confirmationName, setConfirmationName] = useState("");
  const [confirmationPhrase, setConfirmationPhrase] = useState("");
  const [reason, setReason] = useState("");
  const [acknowledgedCodes, setAcknowledgedCodes] = useState<string[]>([]);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const snapshot = preview.data;
  const overriddenBlockers = useMemo(
    () =>
      snapshot
        ? [
            ...new Map(
              snapshot.overridden_blockers.map((blocker) => [
                blocker.code,
                blocker,
              ]),
            ).values(),
          ]
        : [],
    [snapshot],
  );
  const valid = forcePurgeConfirmationIsValid(
    snapshot,
    confirmationName,
    confirmationPhrase,
    reason,
    acknowledgedCodes,
  );

  if (preview.isLoading) {
    return (
      <p className="mt-4 text-sm text-slate-600" role="status">
        Calculating the administrative force-purge impact…
      </p>
    );
  }

  if (preview.isError && !snapshot) {
    return (
      <div className="mt-4 rounded-lg border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900">
        <p role="alert">The force-purge impact could not be loaded.</p>
        <button
          type="button"
          className="btn-secondary mt-3"
          onClick={() => void preview.refetch()}
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          Retry force preview
        </button>
      </div>
    );
  }

  if (!snapshot) return null;

  const hardBlocked = forcePurgeHasHardBlock(snapshot);
  const removedItemCount = snapshot.request_rewrites.reduce(
    (total, rewrite) => total + rewrite.removed_item_count,
    0,
  );
  const removedDiscrepancyCount = snapshot.request_rewrites.reduce(
    (total, rewrite) => total + rewrite.removed_discrepancy_count,
    0,
  );

  return (
    <section
      className="mt-4 rounded-xl border-2 border-rose-400 bg-rose-50 p-4"
      aria-labelledby="force-purge-title"
    >
      <div className="flex items-start gap-3">
        <ShieldAlert
          className="mt-0.5 h-6 w-6 shrink-0 text-rose-800"
          aria-hidden="true"
        />
        <div>
          <h3 id="force-purge-title" className="font-semibold text-rose-950">
            Administrative force purge
          </h3>
          <p className="mt-1 text-sm text-rose-900">
            This escalation overrides the listed safeguards and surgically
            rewrites connected request data. Review every impact below.
          </p>
        </div>
      </div>

      {conflictMessage && (
        <div
          className="mt-4 rounded-lg border border-amber-400 bg-amber-50 p-3 text-sm text-amber-950"
          role="status"
        >
          <p className="font-semibold">The lot graph changed.</p>
          <p className="mt-1">{conflictMessage}</p>
          <p className="mt-1">
            The latest impact is shown. Re-enter both exact confirmations and
            acknowledge every safeguard again.
          </p>
        </div>
      )}

      {hardBlocked && (
        <div className="mt-4 rounded-lg border border-rose-400 bg-white p-4">
          <h4 className="font-semibold text-rose-950">
            Force purge is hard-blocked
          </h4>
          <p className="mt-1 text-sm text-rose-800">
            Merge relationships and invalid Lot identity cannot be overridden.
            No destructive action is available.
          </p>
          <ul className="mt-3 space-y-3">
            {snapshot.hard_blockers.map((blocker) => (
              <li key={blocker.code} className="text-sm text-rose-950">
                <p className="font-medium">
                  {blocker.code.replaceAll("_", " ")}
                </p>
                <p>{blocker.message}</p>
                <ForceBlockerEntities blocker={blocker} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <dl className="mt-4 grid gap-3 rounded-lg bg-white p-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <ImpactMetric
          label="Selected boxes deleted"
          value={String(snapshot.active_box_count + snapshot.archived_box_count)}
        />
        <ImpactMetric
          label="Pallets deleted"
          value={String(snapshot.active_pallet_count + snapshot.archived_pallet_count)}
        />
        <ImpactMetric
          label="Selected items removed"
          value={String(removedItemCount)}
        />
        <ImpactMetric
          label="Full requests deleted"
          value={String(snapshot.fully_deleted_request_count)}
        />
        <ImpactMetric
          label="Requests rewritten"
          value={String(snapshot.request_rewrite_count)}
        />
        <ImpactMetric
          label="Discrepancies removed"
          value={String(removedDiscrepancyCount)}
        />
        <ImpactMetric
          label="Lineage links detached"
          value={String(snapshot.incoming_lineage_detachment_count)}
        />
        <ImpactMetric
          label="Deletable objects"
          value={String(snapshot.object_cleanup.deletable_key_count)}
        />
        <ImpactMetric
          label="Shared objects skipped"
          value={String(snapshot.object_cleanup.shared_skipped_key_count)}
        />
      </dl>

      <section className="mt-4 rounded-lg bg-white p-4">
        <h4 className="text-sm font-semibold">Selected boxes and items deleted</h4>
        <p className="mt-1 text-sm text-slate-700">
          {snapshot.active_box_count} active and {snapshot.archived_box_count}{" "}
          archived selected-lot boxes are deleted. Their selected request items
          are removed; retained items are repositioned.
        </p>
        <IdList
          label="Active box IDs"
          ids={snapshot.active_box_ids}
          total={snapshot.active_box_count}
          truncated={snapshot.active_box_ids_truncated}
        />
        <IdList
          label="Archived box IDs"
          ids={snapshot.archived_box_ids}
          total={snapshot.archived_box_count}
          truncated={snapshot.archived_box_ids_truncated}
        />
        <IdList
          label="Active pallet IDs"
          ids={snapshot.active_pallet_ids}
          total={snapshot.active_pallet_count}
          truncated={snapshot.active_pallet_ids_truncated}
        />
        <IdList
          label="Archived pallet IDs"
          ids={snapshot.archived_pallet_ids}
          total={snapshot.archived_pallet_count}
          truncated={snapshot.archived_pallet_ids_truncated}
        />
      </section>

      <section className="mt-3 rounded-lg bg-white p-4">
        <h4 className="text-sm font-semibold">Full requests deleted</h4>
        <p className="mt-1 text-sm text-slate-700">
          Requests left with no items are deleted with their owned records.
        </p>
        <IdList
          label="Deleted request IDs"
          ids={snapshot.fully_deleted_request_ids}
          total={snapshot.fully_deleted_request_count}
          truncated={snapshot.fully_deleted_request_ids_truncated}
        />
      </section>

      <section className="mt-3 rounded-lg bg-white p-4">
        <h4 className="text-sm font-semibold">
          Mixed and connected requests preserved and rewritten
        </h4>
        {snapshot.request_rewrites.length === 0 ? (
          <p className="mt-1 text-sm text-slate-600">
            No request requires a partial rewrite.
          </p>
        ) : (
          <ul className="mt-3 space-y-4">
            {snapshot.request_rewrites.map((rewrite) => (
              <li
                key={rewrite.request_id}
                className="border-l-2 border-amber-300 pl-3 text-sm"
              >
                <Link
                  to={`/requests/${rewrite.request_id}`}
                  className="font-semibold text-brand-700 hover:underline"
                >
                  Request #{rewrite.request_id}
                </Link>
                <p className="mt-1 text-slate-700">
                  Items {forcePurgeChange(
                    rewrite.before_item_count,
                    rewrite.after_item_count,
                  )}{" "}
                  · Quantity{" "}
                  {forcePurgeChange(
                    rewrite.before_quantity,
                    rewrite.after_quantity,
                  )}{" "}
                  · Actual{" "}
                  {forcePurgeChange(
                    rewrite.before_actual_received_quantity,
                    rewrite.after_actual_received_quantity,
                  )}{" "}
                  · Variance{" "}
                  {forcePurgeChange(
                    rewrite.before_variance_quantity,
                    rewrite.after_variance_quantity,
                  )}
                </p>
                <p className="mt-1 text-slate-600">
                  {rewrite.removed_item_count} selected item
                  {rewrite.removed_item_count === 1 ? "" : "s"} and{" "}
                  {rewrite.removed_discrepancy_count} discrepancy record
                  {rewrite.removed_discrepancy_count === 1 ? "" : "s"} removed.
                </p>
                <p className="mt-1 font-medium text-emerald-800">
                  {forcePurgeSiblingPreservationText(rewrite)}
                </p>
                {rewrite.preserved_sibling_lot_ids.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-2">
                    {rewrite.preserved_sibling_lot_ids.map((id) => (
                      <Link
                        key={id}
                        to={`/lots/${id}`}
                        className="text-brand-700 underline"
                      >
                        Sibling Lot #{id}
                      </Link>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-sm font-medium text-emerald-800">
          Sibling lots, their boxes, and preserved requests are not deleted.
        </p>
      </section>

      <section className="mt-3 rounded-lg bg-white p-4">
        <h4 className="text-sm font-semibold">Lineage links detached</h4>
        {snapshot.incoming_lineage_detachments.length === 0 ? (
          <p className="mt-1 text-sm text-slate-600">
            No preserved request lineage link needs detaching.
          </p>
        ) : (
          <ul className="mt-2 space-y-1 text-sm text-slate-700">
            {snapshot.incoming_lineage_detachments.map((detachment) => (
              <li
                key={`${detachment.request_id}:${detachment.field_name}:${detachment.deleted_target_request_id}`}
              >
                <Link
                  to={`/requests/${detachment.request_id}`}
                  className="text-brand-700 underline"
                >
                  Preserved request #{detachment.request_id}
                </Link>{" "}
                detaches {detachment.field_name.replaceAll("_", " ")} from
                deleted request #{detachment.deleted_target_request_id}.
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-3 rounded-lg bg-white p-4">
        <h4 className="text-sm font-semibold">Stored object cleanup</h4>
        <p className="mt-1 text-sm text-slate-700">
          {snapshot.object_cleanup.deletable_key_count} exclusively owned object
          {snapshot.object_cleanup.deletable_key_count === 1 ? "" : "s"} can be
          deleted. {snapshot.object_cleanup.shared_skipped_key_count} shared
          object{snapshot.object_cleanup.shared_skipped_key_count === 1 ? "" : "s"}{" "}
          will be skipped and preserved.
        </p>
      </section>

      {!hardBlocked && (
        <form
          className="mt-4 rounded-lg border border-rose-400 bg-white p-4"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!valid) return;
            setError(null);
            setConflictMessage(null);
            try {
              const result = await execute.mutateAsync({
                lotId: snapshot.lot_id,
                payload: lotForcePurgePayload(
                  snapshot,
                  confirmationName,
                  confirmationPhrase,
                  reason,
                  acknowledgedCodes,
                ),
              });
              onSuccess(result);
            } catch (caught) {
              const conflict = lotForcePurgeConflict(caught);
              if (conflict) {
                const reset = forcePurgeConflictFormState(reason);
                setConfirmationName(reset.confirmationName);
                setConfirmationPhrase(reset.confirmationPhrase);
                setReason(reset.reason);
                setAcknowledgedCodes(reset.acknowledgedBlockerCodes);
                setConflictMessage(conflict.message);
                if (conflict.current_preview) {
                  queryClient.setQueryData(
                    queryKeys.lotForcePurgePreview(lotId),
                    conflict.current_preview,
                  );
                }
                return;
              }
              setError(
                errorDetail(caught, "The administrative force purge failed."),
              );
            }
          }}
        >
          <div className="flex items-start gap-2 text-rose-950">
            <AlertOctagon className="h-5 w-5 shrink-0" aria-hidden="true" />
            <div>
              <h4 className="font-semibold">Independent confirmations required</h4>
              <p className="mt-1 text-sm">
                Every field and safeguard acknowledgement is exact and
                case-sensitive.
              </p>
            </div>
          </div>

          <label className="mt-4 block">
            <span className="text-xs font-medium text-slate-700">
              Exact Lot name: <code>{snapshot.lot_name}</code>
            </span>
            <input
              autoComplete="off"
              className="input mt-1 font-mono"
              value={confirmationName}
              onChange={(event) => setConfirmationName(event.target.value)}
              disabled={execute.isPending}
            />
          </label>

          <label className="mt-4 block">
            <span className="text-xs font-medium text-slate-700">
              Exact server phrase: <code>{snapshot.confirmation_phrase}</code>
            </span>
            <input
              autoComplete="off"
              className="input mt-1 font-mono"
              value={confirmationPhrase}
              onChange={(event) => setConfirmationPhrase(event.target.value)}
              disabled={execute.isPending}
            />
          </label>

          <label className="mt-4 block">
            <span className="text-xs font-medium text-slate-700">
              Detailed administrative reason (minimum{" "}
              {FORCE_PURGE_MIN_REASON_LENGTH} characters)
            </span>
            <textarea
              required
              minLength={FORCE_PURGE_MIN_REASON_LENGTH}
              maxLength={2000}
              rows={4}
              className="input mt-1"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              disabled={execute.isPending}
              placeholder="Explain why overriding every listed safeguard is necessary"
            />
          </label>

          <fieldset className="mt-4">
            <legend className="text-sm font-semibold text-slate-900">
              Acknowledge each overridden safeguard
            </legend>
            <div className="mt-2 space-y-2">
              {overriddenBlockers.map((blocker) => (
                <label
                  key={blocker.code}
                  className="flex items-start gap-2 rounded border border-rose-200 p-3 text-sm"
                >
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={acknowledgedCodes.includes(blocker.code)}
                    disabled={execute.isPending}
                    onChange={(event) =>
                      setAcknowledgedCodes((current) =>
                        event.target.checked
                          ? [...current, blocker.code]
                          : current.filter((code) => code !== blocker.code),
                      )
                    }
                  />
                  <span>
                    <span className="font-semibold">
                      {blocker.code.replaceAll("_", " ")}
                    </span>
                    <span className="mt-0.5 block text-slate-600">
                      {blocker.message}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          {error && (
            <p role="alert" className="mt-3 text-sm text-rose-700">
              {error}
            </p>
          )}

          <div className="mt-5 flex justify-end">
            <button
              type="submit"
              className="btn bg-rose-950 text-white ring-rose-950 hover:bg-black"
              disabled={!valid || execute.isPending}
            >
              <AlertOctagon className="h-4 w-4" aria-hidden="true" />
              {execute.isPending
                ? "Executing administrative force purge…"
                : "Execute administrative force purge"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function ImpactMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 font-semibold text-slate-900">{value}</dd>
    </div>
  );
}

function IdList({
  label,
  ids,
  total,
  truncated,
}: {
  label: string;
  ids: number[];
  total: number;
  truncated: boolean;
}) {
  return (
    <p className="mt-2 break-words text-xs text-slate-600">
      <span className="font-medium">{label}: </span>
      {ids.length > 0 ? ids.map((id) => `#${id}`).join(", ") : "none"}
      {truncated ? ` (showing ${ids.length} of ${total})` : ""}
    </p>
  );
}

function ForceBlockerEntities({
  blocker,
}: {
  blocker: LotForcePurgeBlocker;
}) {
  if (blocker.entity_ids.length === 0) return null;
  const lotEntity =
    blocker.code === "merged_tombstone" ||
    blocker.code === "merge_target" ||
    blocker.code === "invalid_identity";
  return (
    <div className="mt-1 flex flex-wrap gap-2 text-xs">
      {blocker.entity_ids.map((id) =>
        lotEntity ? (
          <Link key={id} to={`/lots/${id}`} className="text-brand-700 underline">
            Lot #{id}
          </Link>
        ) : (
          <span key={id}>Entity #{id}</span>
        ),
      )}
      {blocker.entity_ids_truncated && (
        <span>
          showing {blocker.entity_ids.length} of {blocker.entity_count}
        </span>
      )}
    </div>
  );
}

function errorDetail(error: unknown, fallback: string): string {
  const detail = (
    error as { response?: { data?: { detail?: unknown } } }
  ).response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}
