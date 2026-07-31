import type { BoxRequest, User } from "@/api/types";

export function requestPermissions(request: BoxRequest, user?: User) {
  const isAdmin = user?.role === "admin";
  const canMove = isAdmin || user?.role === "warehouse_mover";
  const isRequester = user?.id === request.requester_user_id;
  const isSelfReceipt =
    ["xlsx_import", "manual_entry"].includes(request.origin) &&
    request.status === "completed";
  return {
    canMove,
    isRequester,
    canApprove: canMove && request.status === "submitted",
    canUpload:
      (canMove && ["approved", "in_transit"].includes(request.status)) ||
      (isSelfReceipt && (isAdmin || isRequester)),
    canCancel:
      (isAdmin || isRequester) &&
      ["submitted", "approved"].includes(request.status),
    canComplete:
      request.status === "in_transit" &&
      (request.direction === "inbound" ? isAdmin || isRequester : canMove),
  };
}
