import type { BoxRequest, User } from "@/api/types";

export function requestPermissions(request: BoxRequest, user?: User) {
  const isAdmin = user?.role === "admin";
  const canMove = isAdmin || user?.role === "warehouse_mover";
  const isRequester = user?.id === request.requester_user_id;
  const isSelfReceipt =
    ["xlsx_import", "manual_entry"].includes(request.origin);
  const exception = request.current_exception;
  const exceptionFree = !exception;
  return {
    canMove,
    isRequester,
    canApprove:
      request.status === "submitted" && (isSelfReceipt ? isAdmin : canMove),
    canUpload:
      (canMove &&
        [
          "approved",
          "preparing",
          "ready_for_transport",
          "in_transit",
          "awaiting_confirmation",
        ].includes(request.status)) ||
      (isSelfReceipt &&
        ["submitted", "completed"].includes(request.status) &&
        (isAdmin || isRequester)),
    canCancel:
      exceptionFree &&
      (isAdmin || isRequester) &&
      ["submitted", "approved", "preparing", "ready_for_transport"].includes(
        request.status,
      ),
    canPrepare:
      canMove && exceptionFree && request.status === "approved",
    canMarkReady:
      canMove && exceptionFree && request.status === "preparing",
    canStartTransit:
      canMove && exceptionFree && request.status === "ready_for_transport",
    canMarkArrived:
      canMove && exceptionFree && request.status === "in_transit",
    canComplete:
      exceptionFree &&
      request.status === "awaiting_confirmation" &&
      (request.direction === "inbound" ? isAdmin || isRequester : canMove),
    canHold:
      canMove &&
      exceptionFree &&
      [
        "submitted",
        "approved",
        "preparing",
        "ready_for_transport",
        "in_transit",
        "awaiting_confirmation",
      ].includes(request.status),
    canResume:
      canMove && exception?.exception_kind === "hold",
    canReschedule:
      canMove &&
      exceptionFree &&
      [
        "submitted",
        "approved",
        "preparing",
        "ready_for_transport",
        "in_transit",
        "awaiting_confirmation",
      ].includes(request.status),
    canReportFailed:
      canMove && exceptionFree && request.status === "in_transit",
    canRetry:
      canMove && exception?.exception_kind === "failed_delivery",
  };
}
