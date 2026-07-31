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
