import type { BoxRequest, RequestConflict } from "@/api/types";

export function canRetryRequestConflict(
  request: Pick<BoxRequest, "status" | "version">,
  detail: RequestConflict,
): boolean {
  return (
    request.status === detail.latest_status &&
    request.version === detail.latest_version
  );
}

export function conflictDetail(error: unknown): RequestConflict | null {
  const response = (
    error as {
      response?: { status?: number; data?: { detail?: unknown } };
    }
  )?.response;
  const detail = response?.data?.detail;
  if (
    response?.status === 409 &&
    detail &&
    typeof detail === "object" &&
    "latest_version" in detail &&
    "latest_status" in detail
  ) {
    return detail as RequestConflict;
  }
  return null;
}
