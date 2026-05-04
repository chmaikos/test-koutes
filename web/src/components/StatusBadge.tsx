import clsx from "clsx";
import type { BoxStatus } from "@/api/types";

const STATUS_LABELS: Record<BoxStatus, string> = {
  received: "Received",
  in_progress: "In progress",
  processing_complete: "Processing complete",
  ready_to_return: "Ready to return",
  returned: "Returned",
};

const STATUS_CLASSES: Record<BoxStatus, string> = {
  received: "bg-sky-100 text-sky-700",
  in_progress: "bg-amber-100 text-amber-700",
  processing_complete: "bg-violet-100 text-violet-700",
  ready_to_return: "bg-emerald-100 text-emerald-700",
  returned: "bg-slate-200 text-slate-600",
};

export function StatusBadge({ status }: { status: BoxStatus }) {
  return (
    <span className={clsx("badge", STATUS_CLASSES[status])}>
      {STATUS_LABELS[status]}
    </span>
  );
}

export const STATUS_LABEL = STATUS_LABELS;
