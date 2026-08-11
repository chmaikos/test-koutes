import { useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { useRetryLotPurgeCleanup } from "@/api/hooks";
import type {
  LotPurgeCleanupStatus,
  LotPurgeObjectFailure,
} from "@/api/types";
import {
  purgeCleanupWarning,
  purgeSuccessAction,
} from "@/pages/lots";

export interface PurgeCleanupState {
  auditId: number;
  purgeMode: "safe" | "force";
  status: LotPurgeCleanupStatus;
  failureCount: number;
  failures: LotPurgeObjectFailure[];
}

export function PurgeCleanupResultPanel({
  cleanup,
  onCleanupChange,
  onNavigateToLots,
}: {
  cleanup: PurgeCleanupState;
  onCleanupChange: (cleanup: PurgeCleanupState) => void;
  onNavigateToLots: () => void;
}) {
  const retryCleanup = useRetryLotPurgeCleanup();
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="modal-backdrop z-50" role="presentation">
      <section
        className="modal-sheet max-w-lg"
        role="dialog"
        aria-modal="true"
        aria-labelledby="purge-result-title"
        aria-describedby="purge-result-description"
      >
        <div className="flex items-start gap-3">
          <AlertTriangle
            className="mt-0.5 h-6 w-6 shrink-0 text-amber-600"
            aria-hidden="true"
          />
          <div>
            <h2 id="purge-result-title" className="text-lg font-semibold">
              Lot removed; storage cleanup incomplete
            </h2>
            <p
              id="purge-result-description"
              className="mt-1 text-sm text-slate-700"
            >
              {purgeCleanupWarning(
                cleanup.auditId,
                cleanup.status,
                cleanup.failureCount,
              )}
            </p>
          </div>
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-3 rounded-lg bg-slate-50 p-3 text-sm">
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-500">
              Purge mode
            </dt>
            <dd className="font-semibold">
              {cleanup.purgeMode === "force"
                ? "Administrative force"
                : "Safe"}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-500">
              Audit ID
            </dt>
            <dd className="font-mono font-semibold">#{cleanup.auditId}</dd>
          </div>
        </dl>

        <div
          className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
          role="status"
        >
          <p className="font-semibold">
            Cleanup status: {cleanup.status.replaceAll("_", " ")}
          </p>
          {cleanup.failures.length > 0 ? (
            <ul className="mt-2 space-y-1">
              {cleanup.failures.map((failure) => (
                <li
                  key={`${failure.object_key}:${failure.error}`}
                  className="break-all"
                >
                  <span className="font-mono text-xs">
                    {failure.object_key}
                  </span>
                  : {failure.error}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1">
              {cleanup.failureCount > 0
                ? `${cleanup.failureCount} object cleanup failure${
                    cleanup.failureCount === 1 ? "" : "s"
                  } were reported; retry to refresh the details.`
                : "Cleanup is still pending; no individual object failure was reported."}
            </p>
          )}
        </div>

        {error && (
          <p role="alert" className="mt-3 text-sm text-rose-700">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end">
          <button
            type="button"
            className="btn-primary"
            disabled={retryCleanup.isPending}
            onClick={async () => {
              setError(null);
              try {
                const result = await retryCleanup.mutateAsync(cleanup.auditId);
                if (
                  purgeSuccessAction(result.object_cleanup_status) ===
                  "navigate"
                ) {
                  onNavigateToLots();
                  return;
                }
                onCleanupChange({
                  ...cleanup,
                  status: result.object_cleanup_status,
                  failureCount: result.object_cleanup_failures.length,
                  failures: result.object_cleanup_failures,
                });
              } catch (caught) {
                setError(errorDetail(caught, "Cleanup retry failed."));
              }
            }}
          >
            <RefreshCw
              className={`h-4 w-4 ${
                retryCleanup.isPending ? "animate-spin" : ""
              }`}
              aria-hidden="true"
            />
            {retryCleanup.isPending
              ? "Retrying cleanup…"
              : "Retry storage cleanup"}
          </button>
        </div>
      </section>
    </div>
  );
}

function errorDetail(error: unknown, fallback: string): string {
  const detail = (
    error as { response?: { data?: { detail?: unknown } } }
  ).response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}
