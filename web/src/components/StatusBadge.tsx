import clsx from "clsx";
import type { BoxStatus } from "@/api/types";

const STATUS_LABELS: Record<BoxStatus, string> = {
  quarantined: "Quarantined",
  received: "Received",
  processing: "Processing",
  incomplete: "Incomplete",
  ready_to_return: "Ready to return",
  returned: "Returned",
};

// Colour the badges along an intuitive gradient: sky (just arrived) ->
// indigo (open & working) -> amber (waiting for downstream) -> emerald
// (packaged) -> slate (gone). The classes intentionally avoid red because
// `incomplete` is a normal pipeline state, not an error.
const STATUS_CLASSES: Record<BoxStatus, string> = {
  quarantined: "bg-amber-100 text-amber-800",
  received: "bg-sky-100 text-sky-700",
  processing: "bg-indigo-100 text-indigo-700",
  incomplete: "bg-amber-100 text-amber-800",
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
