import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  queryKeys,
  useLotPurgePreview,
  usePurgeLot,
} from "@/api/hooks";
import type { LotPurgePreview, LotSummary } from "@/api/types";
import { ForcePurgeEscalationPanel } from "@/components/ForcePurgeEscalationPanel";
import {
  PurgeCleanupResultPanel,
  type PurgeCleanupState,
} from "@/components/PurgeCleanupResultPanel";
import {
  lotPurgeBlockerText,
  lotPurgeConflict,
  lotPurgeEntityPath,
  lotPurgePayload,
  lotPurgeRemovalItems,
  purgeConfirmationIsValid,
  purgeConflictFormState,
  purgeSuccessAction,
} from "@/pages/lots";

export function PurgeLotDialog({
  lot,
  onClose,
  onNavigateToLots,
}: {
  lot: LotSummary;
  onClose: () => void;
  onNavigateToLots: () => void;
}) {
  const queryClient = useQueryClient();
  const preview = useLotPurgePreview(lot.id, true);
  const purge = usePurgeLot();
  const [confirmationName, setConfirmationName] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);
  const [forceExpanded, setForceExpanded] = useState(false);
  const [cleanup, setCleanup] = useState<PurgeCleanupState | null>(null);
  const snapshot = preview.data;
  const safeValid = purgeConfirmationIsValid(
    snapshot,
    confirmationName,
    reason,
  );

  if (cleanup) {
    return (
      <PurgeCleanupResultPanel
        cleanup={cleanup}
        onCleanupChange={setCleanup}
        onNavigateToLots={onNavigateToLots}
      />
    );
  }

  return (
    <div className="modal-backdrop z-50" role="presentation">
      <section
        className="modal-sheet max-w-3xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="purge-lot-title"
        aria-describedby="purge-lot-warning"
      >
        <div className="flex items-start gap-3">
          <AlertTriangle
            className="mt-0.5 h-6 w-6 shrink-0 text-rose-600"
            aria-hidden="true"
          />
          <div>
            <h2 id="purge-lot-title" className="text-lg font-semibold">
              Permanently purge lot
            </h2>
            <p
              id="purge-lot-warning"
              className="mt-1 text-sm font-medium text-rose-700"
            >
              Permanent deletion cannot be undone. Safe purge is evaluated
              first; administrative force escalation is separate and available
              only when safe purge is blocked.
            </p>
          </div>
        </div>

        {preview.isLoading && (
          <p className="mt-5 text-sm text-slate-500" role="status">
            Checking the latest safe-purge eligibility…
          </p>
        )}
        {preview.isError && !snapshot && (
          <div className="mt-5 rounded-lg bg-rose-50 p-3 text-sm text-rose-800">
            <p role="alert">The purge preview could not be loaded.</p>
            <button
              type="button"
              className="btn-secondary mt-3"
              onClick={() => void preview.refetch()}
            >
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              Retry preview
            </button>
          </div>
        )}

        {snapshot?.eligible && snapshot.blockers.length === 0 && (
          <form
            className="mt-5"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!safeValid) return;
              setError(null);
              setConflictMessage(null);
              try {
                const result = await purge.mutateAsync({
                  lotId: snapshot.lot_id,
                  payload: lotPurgePayload(
                    snapshot,
                    confirmationName,
                    reason,
                  ),
                });
                if (
                  purgeSuccessAction(result.object_cleanup_status) ===
                  "navigate"
                ) {
                  onNavigateToLots();
                  return;
                }
                setCleanup({
                  auditId: result.purge_audit_id,
                  purgeMode: "safe",
                  status: result.object_cleanup_status,
                  failureCount: result.object_cleanup_failures.length,
                  failures: result.object_cleanup_failures,
                });
              } catch (caught) {
                const conflict = lotPurgeConflict(caught);
                if (conflict) {
                  const reset = purgeConflictFormState(reason);
                  setConfirmationName(reset.confirmationName);
                  setReason(reset.reason);
                  setConflictMessage(
                    `${conflict.message} The latest safe preview is loaded. Review it and type the exact lot name again.`,
                  );
                  if (conflict.current_preview) {
                    queryClient.setQueryData(
                      queryKeys.lotPurgePreview(lot.id),
                      conflict.current_preview,
                    );
                  }
                  await preview.refetch();
                  return;
                }
                setError(errorDetail(caught, "The lot could not be purged."));
              }
            }}
          >
            <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700">
              Phase 1 · Safe purge eligible
            </p>
            <SafeImpact preview={snapshot} />

            {conflictMessage && (
              <p
                role="status"
                className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-950"
              >
                {conflictMessage}
              </p>
            )}

            <label className="mt-5 block">
              <span className="text-xs font-medium text-slate-700">
                Type the exact lot name{" "}
                <code className="rounded bg-slate-100 px-1 py-0.5">
                  {snapshot.lot_name}
                </code>
              </span>
              <input
                autoComplete="off"
                className="input mt-1 font-mono"
                value={confirmationName}
                onChange={(event) => setConfirmationName(event.target.value)}
                disabled={purge.isPending}
                aria-describedby="purge-confirmation-policy"
              />
              <span
                id="purge-confirmation-policy"
                className="mt-1 block text-xs text-slate-500"
              >
                Case-sensitive, with no trimming or normalization.
              </span>
            </label>

            <label className="mt-4 block">
              <span className="text-xs font-medium text-slate-700">
                Audit reason
              </span>
              <textarea
                required
                maxLength={2000}
                rows={3}
                className="input mt-1"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                disabled={purge.isPending}
                placeholder="Required; explain why permanent removal is necessary"
              />
            </label>

            {error && (
              <p role="alert" className="mt-3 text-sm text-rose-700">
                {error}
              </p>
            )}

            <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button
                type="button"
                className="btn-secondary"
                disabled={purge.isPending}
                onClick={onClose}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn-danger"
                disabled={!safeValid || purge.isPending}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                {purge.isPending
                  ? "Permanently deleting…"
                  : "Permanently delete lot and receipts"}
              </button>
            </div>
          </form>
        )}

        {snapshot && (!snapshot.eligible || snapshot.blockers.length > 0) && (
          <div className="mt-5">
            <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">
              Phase 1 · Safe purge blocked
            </p>
            <SafeImpact preview={snapshot} />
            <SafeBlockers preview={snapshot} />

            <section className="mt-5 border-t border-rose-200 pt-5">
              <button
                type="button"
                className="flex w-full items-center justify-between rounded-lg border border-rose-300 bg-white p-4 text-left text-rose-950 hover:bg-rose-50"
                aria-expanded={forceExpanded}
                onClick={() => setForceExpanded((current) => !current)}
              >
                <span className="flex items-center gap-2 font-semibold">
                  <ShieldAlert className="h-5 w-5" aria-hidden="true" />
                  Review administrative force purge
                </span>
                {forceExpanded ? (
                  <ChevronUp className="h-5 w-5" aria-hidden="true" />
                ) : (
                  <ChevronDown className="h-5 w-5" aria-hidden="true" />
                )}
              </button>
              <p className="mt-2 text-xs text-slate-600">
                The force impact is fetched only after this disclosure is
                expanded.
              </p>
              {forceExpanded && (
                <ForcePurgeEscalationPanel
                  lotId={lot.id}
                  onSuccess={(result) => {
                    if (
                      purgeSuccessAction(result.object_cleanup_status) ===
                      "navigate"
                    ) {
                      onNavigateToLots();
                      return;
                    }
                    setCleanup({
                      auditId: result.purge_audit_id,
                      purgeMode: "force",
                      status: result.object_cleanup_status,
                      failureCount: result.object_cleanup_failure_count,
                      failures: [],
                    });
                  }}
                />
              )}
            </section>

            <div className="mt-6 flex justify-end">
              <button type="button" className="btn-secondary" onClick={onClose}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function SafeImpact({ preview }: { preview: LotPurgePreview }) {
  return (
    <>
      <dl className="mt-3 grid gap-3 rounded-lg bg-slate-50 p-4 text-sm sm:grid-cols-2">
        <PreviewMetric
          label="Lot"
          value={`${preview.lot_name} · version ${preview.lot_version}`}
        />
        <PreviewMetric
          label="Archived boxes"
          value={String(preview.archived_box_count)}
        />
        <PreviewMetric
          label="Exclusive self-receipts"
          value={String(preview.linked_request_count)}
        />
        <PreviewMetric
          label="Stored objects"
          value={String(preview.object_key_count)}
        />
      </dl>

      {preview.archived_box_ids.length > 0 && (
        <EntityLinks
          label="Archived boxes"
          ids={preview.archived_box_ids}
          path={(id) => `/boxes/${id}`}
          truncated={preview.archived_box_ids_truncated}
          total={preview.archived_box_count}
        />
      )}
      {preview.linked_request_ids.length > 0 && (
        <EntityLinks
          label="Exclusive self-receipts"
          ids={preview.linked_request_ids}
          path={(id) => `/requests/${id}`}
          truncated={preview.linked_request_ids_truncated}
          total={preview.linked_request_count}
        />
      )}

      <section className="mt-5">
        <h3 className="text-sm font-semibold">Exactly what safe purge removes</h3>
        <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-slate-700">
          {lotPurgeRemovalItems(preview).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-slate-500">
          Owned request items, events, documents, attachments, comments,
          discrepancies, notifications, and email outbox records are included
          with the listed self-receipts. The independent purge audit record is
          retained.
        </p>
      </section>
    </>
  );
}

function SafeBlockers({ preview }: { preview: LotPurgePreview }) {
  return (
    <section
      className="mt-5 rounded-lg border border-amber-300 bg-amber-50 p-4"
      aria-labelledby="purge-blockers-title"
    >
      <h3
        id="purge-blockers-title"
        className="text-sm font-semibold text-amber-950"
      >
        Existing safe-purge blockers
      </h3>
      <ul className="mt-3 space-y-3">
        {preview.blockers.map((blocker) => (
          <li key={blocker.code} className="text-sm text-amber-950">
            <p className="font-medium">{lotPurgeBlockerText(blocker)}</p>
            <p className="mt-0.5 text-xs">{blocker.message}</p>
            {blocker.entities.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs">
                {blocker.entities.map((entity) => (
                  <Link
                    key={`${entity.entity_type}:${entity.entity_id}`}
                    className="text-brand-700 underline"
                    to={lotPurgeEntityPath(entity, preview.lot_id)}
                  >
                    {entity.entity_type} #{entity.entity_id}
                  </Link>
                ))}
                {blocker.entities_truncated && (
                  <span>
                    Showing {blocker.entities.length} of {blocker.entity_count}
                  </span>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function PreviewMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 font-medium text-slate-900">{value}</dd>
    </div>
  );
}

function EntityLinks({
  label,
  ids,
  path,
  truncated,
  total,
}: {
  label: string;
  ids: number[];
  path: (id: number) => string;
  truncated: boolean;
  total: number;
}) {
  return (
    <div className="mt-3 text-sm">
      <span className="font-medium text-slate-700">{label}: </span>
      <span className="inline-flex flex-wrap gap-x-2 gap-y-1">
        {ids.map((id) => (
          <Link
            key={id}
            className="text-brand-700 underline"
            to={path(id)}
          >
            #{id}
          </Link>
        ))}
        {truncated && (
          <span className="text-slate-500">
            showing {ids.length} of {total}
          </span>
        )}
      </span>
    </div>
  );
}

function errorDetail(error: unknown, fallback: string): string {
  const detail = (
    error as { response?: { data?: { detail?: unknown } } }
  ).response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}
