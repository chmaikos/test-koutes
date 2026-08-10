import clsx from "clsx";
import type { RequestDirection, RequestStatus } from "@/api/types";

export const REQUEST_STATUS_LABEL: Record<RequestStatus, string> = {
  draft: "Draft",
  submitted: "Submitted",
  approved: "Approved",
  preparing: "Preparing",
  ready_for_transport: "Ready for transport",
  in_transit: "In transit",
  awaiting_confirmation: "Awaiting confirmation",
  completed: "Completed",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

const REQUEST_STATUS_CLASS: Record<RequestStatus, string> = {
  draft: "bg-slate-100 text-slate-700",
  submitted: "bg-sky-100 text-sky-700",
  approved: "bg-indigo-100 text-indigo-700",
  preparing: "bg-violet-100 text-violet-700",
  ready_for_transport: "bg-cyan-100 text-cyan-800",
  in_transit: "bg-amber-100 text-amber-800",
  awaiting_confirmation: "bg-orange-100 text-orange-800",
  completed: "bg-emerald-100 text-emerald-700",
  rejected: "bg-rose-100 text-rose-700",
  cancelled: "bg-slate-200 text-slate-600",
};

export const REQUEST_DIRECTION_LABEL: Record<RequestDirection, string> = {
  inbound: "Inbound order",
  return: "Box return",
};

export function requestStatusLabel(
  status: RequestStatus,
  direction?: RequestDirection,
) {
  if (direction && status === "preparing") {
    return direction === "inbound" ? "Preparing inbound" : "Preparing return";
  }
  if (direction && status === "ready_for_transport") {
    return direction === "inbound"
      ? "Ready for dispatch"
      : "Ready for collection";
  }
  if (direction && status === "in_transit") {
    return direction === "inbound" ? "Delivering" : "Collecting";
  }
  if (direction && status === "awaiting_confirmation") {
    return direction === "inbound"
      ? "Delivered · awaiting requester confirmation"
      : "Collected · awaiting warehouse confirmation";
  }
  return REQUEST_STATUS_LABEL[status];
}

export function RequestStatusBadge({
  status,
  direction,
}: {
  status: RequestStatus;
  direction?: RequestDirection;
}) {
  return (
    <span className={clsx("badge", REQUEST_STATUS_CLASS[status])}>
      {requestStatusLabel(status, direction)}
    </span>
  );
}
