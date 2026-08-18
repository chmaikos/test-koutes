export function hasRequiredOverrideReason(
  force: boolean,
  reason: string,
): boolean {
  return !force || reason.trim().length > 0;
}

export function deleteActionLabel(forceArchive: boolean): string {
  return forceArchive ? "Delete or archive" : "Delete unlinked boxes";
}

export function shouldOfferForceArchive(status: number | undefined): boolean {
  return status === 409;
}

export function canRelocateReturnedSelection(
  returnedCount: number,
  hasDestination: boolean,
  isAdmin: boolean,
  force: boolean,
  reason: string,
): boolean {
  if (returnedCount === 0 || !hasDestination) return true;
  if (!isAdmin) return true;
  return force && hasRequiredOverrideReason(true, reason);
}

export function formatCancelledRequestIds(ids: number[]): string {
  return ids.map((requestId) => `#${requestId}`).join(", ");
}
