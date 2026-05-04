import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

export function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  isPending = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  isPending?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="card card-pad w-full max-w-md">
        <div className="flex items-start gap-3">
          {destructive && (
            <div className="rounded-full bg-rose-100 p-2 text-rose-600">
              <AlertTriangle className="h-5 w-5" />
            </div>
          )}
          <div className="flex-1">
            <h2 className="text-base font-semibold">{title}</h2>
            <div className="mt-1 text-sm text-slate-600">{message}</div>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
            disabled={isPending}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={
              destructive
                ? "inline-flex items-center gap-1 rounded-md bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-50"
                : "btn-primary"
            }
            onClick={() => {
              void onConfirm();
            }}
            disabled={isPending}
          >
            {isPending ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
