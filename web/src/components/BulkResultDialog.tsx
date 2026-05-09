import { useMemo, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, X } from "lucide-react";

interface SkipRow {
  primary: string;
  secondary?: string;
  reason: string;
}

export function BulkResultDialog({
  title,
  successLabel,
  successCount,
  skipped,
  onClose,
}: {
  title: string;
  successLabel: string;
  successCount: number;
  skipped: SkipRow[];
  onClose: () => void;
}) {
  const [showSkipped, setShowSkipped] = useState(skipped.length > 0);
  const skippedCount = skipped.length;
  const total = successCount + skippedCount;

  const summary = useMemo(() => {
    if (total === 0) return "No rows processed.";
    if (skippedCount === 0) return `All ${total} rows applied successfully.`;
    if (successCount === 0) return `All ${total} rows were skipped.`;
    return `${successCount} of ${total} rows applied; ${skippedCount} skipped.`;
  }, [successCount, skippedCount, total]);

  return (
    <div className="modal-backdrop">
      <div className="modal-sheet max-w-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">{title}</h2>
            <p className="text-sm text-slate-500">{summary}</p>
          </div>
          <button
            type="button"
            className="btn-ghost"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
            <CheckCircle2 className="mr-1 inline h-4 w-4" />
            {successCount} {successLabel}
          </div>
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
            {skippedCount} skipped
          </div>
        </div>

        {skippedCount > 0 && (
          <div className="mt-3">
            <button
              type="button"
              className="flex items-center gap-1 text-sm font-medium text-slate-700"
              onClick={() => setShowSkipped((s) => !s)}
            >
              {showSkipped ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
              {showSkipped ? "Hide" : "Show"} skip reasons
            </button>
            {showSkipped && (
              <div className="mt-2 max-h-72 overflow-y-auto rounded-md border border-slate-200">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="px-3 py-2 text-left">Row</th>
                      <th className="px-3 py-2 text-left">Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {skipped.map((row, idx) => (
                      <tr key={`${row.primary}-${idx}`}>
                        <td className="px-3 py-2 align-top font-mono">
                          <div>{row.primary}</div>
                          {row.secondary && (
                            <div className="text-xs text-slate-400">
                              {row.secondary}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2 align-top text-slate-600">
                          {row.reason}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        <div className="mt-5 flex justify-end">
          <button type="button" className="btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

export type { SkipRow as BulkResultSkipRow };
