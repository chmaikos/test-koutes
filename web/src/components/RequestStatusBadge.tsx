import clsx from "clsx";
import type { RequestDirection, RequestStatus } from "@/api/types";

export const REQUEST_STATUS_LABEL: Record<RequestStatus, string> = {
  submitted: "Submitted",
  approved: "Approved",
  in_transit: "In transit",
  completed: "Completed",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

const REQUEST_STATUS_CLASS: Record<RequestStatus, string> = {
  submitted: "bg-sky-100 text-sky-700",
  approved: "bg-indigo-100 text-indigo-700",
  in_transit: "bg-amber-100 text-amber-800",
  completed: "bg-emerald-100 text-emerald-700",
  rejected: "bg-rose-100 text-rose-700",
  cancelled: "bg-slate-200 text-slate-600",
};

export const REQUEST_DIRECTION_LABEL: Record<RequestDirection, string> = {
  inbound: "Inbound order",
  return: "Box return",
};

export function RequestStatusBadge({
  status,
  direction,
}: {
  status: RequestStatus;
  direction?: RequestDirection;
}) {
  const label =
    status === "in_transit" && direction
      ? direction === "inbound"
        ? "Delivering"
        : "Collecting"
      : REQUEST_STATUS_LABEL[status];
  return (
    <span className={clsx("badge", REQUEST_STATUS_CLASS[status])}>
      {label}
    </span>
  );
}
