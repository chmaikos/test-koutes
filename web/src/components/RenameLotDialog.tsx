import { useState } from "react";
import { useMergeLots, useRenameLot } from "@/api/hooks";
import type { LotMergeCandidate, LotSummary } from "@/api/types";
import {
  lotConflictCurrent,
  lotMergeCandidate,
  lotMergeConflictCode,
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

  return (
    <div className="modal-backdrop z-40">
      <form
        className="modal-sheet max-w-md"
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
          <div
            role="status"
            className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
          >
            <p className="font-semibold">
              Lot “{candidate.target.name}” already exists.
            </p>
            {candidate.merge_allowed ? (
              <p className="mt-1">
                You may merge “{candidate.source.name}” into it. Every current
                and archived box and current lot link will move to the target;
                historical request and XLSX text remains unchanged. This action
                is explicit and audited.
              </p>
            ) : (
              <>
                <p className="mt-1 font-medium text-rose-700">
                  Merge is blocked because both lots contain the following box
                  numbers:
                </p>
                <ul className="mt-1 list-inside list-disc font-mono text-rose-700">
                  {candidate.overlapping_box_numbers.map((number) => (
                    <li key={number}>{number}</li>
                  ))}
                </ul>
                {candidate.overlap_list_truncated && (
                  <p className="mt-1 text-rose-700">
                    Showing {candidate.overlapping_box_numbers.length} of{" "}
                    {candidate.overlapping_box_count} overlaps.
                  </p>
                )}
                <p className="mt-1">
                  Reassign or otherwise resolve every duplicate physical box
                  number, including archived boxes, then try again.
                </p>
              </>
            )}
          </div>
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
          {candidate ? (
            <button
              type="button"
              className={candidate.merge_allowed ? "btn-danger" : "btn-secondary"}
              disabled={
                !candidate.merge_allowed || merge.isPending || !reason.trim()
              }
              onClick={async () => {
                if (!candidate.merge_allowed) return;
                setError(null);
                setLatestMessage(null);
                try {
                  const result = await merge.mutateAsync({
                    sourceId: lot.id,
                    payload: lotMergePayload(candidate, reason),
                  });
                  onClose();
                  onMerged(result.target.id);
                } catch (caught) {
                  const refreshed = lotMergeCandidate(caught);
                  const code = lotMergeConflictCode(caught);
                  if (
                    refreshed &&
                    (code === "source_version_conflict" ||
                      code === "target_version_conflict" ||
                      code === "box_number_overlap")
                  ) {
                    setCandidate(refreshed);
                    setVersion(refreshed.source.version);
                    setLatestMessage(
                      "The source, target, or box inventory changed. The latest merge check is loaded; review it and explicitly confirm again.",
                    );
                    return;
                  }
                  setCandidate(null);
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
                : candidate.merge_allowed
                  ? `Merge into “${candidate.target.name}”`
                  : "Merge blocked by box overlaps"}
            </button>
          ) : (
            <button
              type="submit"
              className="btn-primary"
              disabled={rename.isPending || !!renameValidation(name, reason)}
            >
              {rename.isPending ? "Saving…" : "Confirm correction"}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

