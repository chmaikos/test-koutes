import { useState } from "react";
import { useRenameLot } from "@/api/hooks";
import type { LotSummary } from "@/api/types";
import {
  lotConflictCurrent,
  lotRenamePayload,
  renameValidation,
} from "@/pages/lots";

export function RenameLotDialog({
  lot,
  onClose,
}: {
  lot: LotSummary;
  onClose: () => void;
}) {
  const rename = useRenameLot();
  const [name, setName] = useState(lot.name);
  const [reason, setReason] = useState("");
  const [version, setVersion] = useState(lot.version);
  const [error, setError] = useState<string | null>(null);
  const [latestMessage, setLatestMessage] = useState<string | null>(null);

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
          try {
            await rename.mutateAsync({
              id: lot.id,
              payload: lotRenamePayload(name, reason, version),
            });
            onClose();
          } catch (caught) {
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
          merge or delete lots.
        </p>
        <label className="mt-4 block">
          <span className="text-xs font-medium text-slate-600">New name</span>
          <input
            autoFocus
            required
            maxLength={64}
            className="input mt-1"
            value={name}
            onChange={(event) => setName(event.target.value)}
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
        {error && (
          <p role="alert" className="mt-3 text-sm text-rose-600">
            {error}
          </p>
        )}
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            className="btn-secondary"
            disabled={rename.isPending}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn-primary"
            disabled={rename.isPending || !!renameValidation(name, reason)}
          >
            {rename.isPending ? "Saving…" : "Confirm correction"}
          </button>
        </div>
      </form>
    </div>
  );
}

