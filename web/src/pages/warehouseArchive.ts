interface ArchiveConflictDetail {
  message?: string;
  active_boxes?: number;
  active_requests?: number;
  last_active_warehouse?: boolean;
}

export function warehouseArchiveError(error: unknown): string {
  const response = (
    error as {
      response?: { data?: { detail?: string | ArchiveConflictDetail } };
    }
  )?.response;
  const detail = response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (!detail || typeof detail !== "object") {
    return error instanceof Error ? error.message : "Warehouse action failed.";
  }

  const blockers: string[] = [];
  if (detail.active_boxes) {
    blockers.push(
      `${detail.active_boxes} active box${detail.active_boxes === 1 ? "" : "es"}`,
    );
  }
  if (detail.active_requests) {
    blockers.push(
      `${detail.active_requests} open request${
        detail.active_requests === 1 ? "" : "s"
      }`,
    );
  }
  if (detail.last_active_warehouse) blockers.push("it is the last active warehouse");
  return blockers.length
    ? `Cannot archive: ${blockers.join(", ")}.`
    : (detail.message ?? "Warehouse action failed.");
}
